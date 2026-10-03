"""Durable workflow records. Transactions never span model calls or git operations."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from factory.domain.workflow import StoryPlan

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_sessions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 backlog_id INTEGER REFERENCES backlog_stories(id), phase TEXT NOT NULL,
 status TEXT NOT NULL, draft TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_session_story ON workflow_sessions(backlog_id)
 WHERE backlog_id IS NOT NULL AND status != 'cancelled';
CREATE TABLE IF NOT EXISTS workflow_decisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT REFERENCES workflow_sessions(id), key TEXT NOT NULL, kind TEXT NOT NULL,
 question TEXT NOT NULL, context TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 answer TEXT, created_at TEXT NOT NULL, answered_at TEXT, UNIQUE(project_id,key)
);
CREATE TABLE IF NOT EXISTS workflow_plans (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 backlog_id INTEGER NOT NULL REFERENCES backlog_stories(id), revision INTEGER NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(backlog_id,revision)
);
CREATE TABLE IF NOT EXISTS workflow_proposals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL,
 key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, proposal_id TEXT REFERENCES workflow_proposals(id),
 run_id INTEGER REFERENCES pipeline_runs(id), status TEXT NOT NULL DEFAULT 'queued',
 owner TEXT, lease_until TEXT, result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_active_run ON workflow_jobs(run_id)
 WHERE run_id IS NOT NULL AND status IN ('queued','running','interrupted');
CREATE TABLE IF NOT EXISTS workflow_controls (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), paused INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS workflow_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
 kind TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_budgets (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), cap_usd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_reservations (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES workflow_budgets(id),
 amount_usd REAL NOT NULL, actual_usd REAL, status TEXT NOT NULL DEFAULT 'reserved'
);
CREATE TABLE IF NOT EXISTS workflow_locks (
 project_id TEXT NOT NULL REFERENCES projects(id), resource TEXT NOT NULL,
 owner TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id,resource)
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(workflow_decisions)")}
    if "draft_text" not in columns:
        conn.execute("ALTER TABLE workflow_decisions ADD COLUMN draft_text TEXT NOT NULL DEFAULT ''")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for key in ('draft', 'context', 'payload', 'result', 'details'):
        if key in value and value[key] is not None:
            value[key] = json.loads(value[key])
    return value


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Acquire write authority before read/decide/write; nested callers share the lock."""
    outer = not conn.in_transaction
    if outer:
        conn.execute('BEGIN IMMEDIATE')
    name = 'workflow_' + _id()
    conn.execute(f'SAVEPOINT {name}')
    try:
        yield
        conn.execute(f'RELEASE SAVEPOINT {name}')
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT {name}')
        conn.execute(f'RELEASE SAVEPOINT {name}')
        raise
    # The caller's get_db context owns the commit, including any adjacent writes.


def _get(conn: sqlite3.Connection, table: str, identity: str) -> dict[str, Any]:
    row = _row(conn.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone())
    if row is None:
        raise ValueError(f'Unknown workflow record: {identity}')
    return row


def _list(conn: sqlite3.Connection, table: str, project_id: str) -> list[dict[str, Any]]:
    return [_row(r) for r in conn.execute(
        f'SELECT * FROM {table} WHERE project_id=? ORDER BY created_at,id', (project_id,)
    ).fetchall()]


def create_session(conn, project_id: str, backlog_id: int | None = None,
                   phase: str = 'product', draft: dict | None = None) -> dict:
    with atomic(conn):
        if backlog_id is not None:
            old = conn.execute("SELECT * FROM workflow_sessions WHERE backlog_id=? "
                               "AND status!='cancelled'", (backlog_id,)).fetchone()
            if old:
                return _row(old)
            row = conn.execute('SELECT project_id FROM backlog_stories WHERE id=?',
                               (backlog_id,)).fetchone()
            if not row or row[0] != project_id:
                raise ValueError('Story does not belong to this project')
        identity, now = _id(), _now()
        conn.execute('INSERT INTO workflow_sessions VALUES (?,?,?,?,?,?,1,?,?)',
                     (identity, project_id, backlog_id, phase, 'refining', _json(draft or {}), now, now))
        return get_session(conn, identity)


def get_session(conn, identity: str) -> dict:
    return _get(conn, 'workflow_sessions', identity)


def list_sessions(conn, project_id: str) -> list[dict]:
    return _list(conn, 'workflow_sessions', project_id)


def update_session(conn, identity: str, *, phase: str, draft: dict,
                   status: str = 'refining', expected_revision: int | None = None) -> dict:
    with atomic(conn):
        old = get_session(conn, identity)
        if expected_revision is not None and old['revision'] != expected_revision:
            raise ValueError('Refinement changed; refresh before saving')
        conn.execute('UPDATE workflow_sessions SET phase=?,draft=?,status=?,revision=revision+1,'
                     'updated_at=? WHERE id=?', (phase, _json(draft), status, _now(), identity))
        return get_session(conn, identity)


def add_decision(conn, project_id: str, key: str, question: str, *, session_id: str | None = None,
                 kind: str = 'question', context: dict | None = None) -> dict:
    with atomic(conn):
        old = conn.execute('SELECT * FROM workflow_decisions WHERE project_id=? AND key=?',
                           (project_id, key)).fetchone()
        if old:
            if old['question'] != question or old['context'] != _json(context or {}):
                raise ValueError('Decision key already identifies different content')
            return _row(old)
        identity = _id()
        conn.execute('INSERT INTO workflow_decisions '
                     '(id,project_id,session_id,key,kind,question,context,created_at) '
                     'VALUES (?,?,?,?,?,?,?,?)',
                     (identity, project_id, session_id, key, kind, question, _json(context or {}), _now()))
        return get_decision(conn, identity)


def get_decision(conn, identity: str) -> dict:
    return _get(conn, 'workflow_decisions', identity)


def list_decisions(conn, project_id: str) -> list[dict]:
    return _list(conn, 'workflow_decisions', project_id)


def answer_decision(conn, identity: str, answer: str) -> dict:
    if not answer.strip():
        raise ValueError('An answer is required')
    with atomic(conn):
        old = get_decision(conn, identity)
        if old['status'] == 'answered':
            if old['answer'] != answer:
                raise ValueError('Already answered; create a new decision to revise it')
            return old
        conn.execute("UPDATE workflow_decisions SET status='answered',answer=?,answered_at=? "
                     'WHERE id=?', (answer, _now(), identity))
        add_event(conn, old['project_id'], 'decision', 'Decision answered', {'decision_id': identity})
        return get_decision(conn, identity)


def save_plan(conn, plan: StoryPlan) -> None:
    with atomic(conn):
        old = conn.execute('SELECT payload FROM workflow_plans WHERE id=?', (plan.id,)).fetchone()
        payload = plan.model_dump_json()
        if old:
            if old[0] != payload:
                raise ValueError('Plans are immutable; save a new revision')
            return
        row = conn.execute('SELECT project_id FROM backlog_stories WHERE id=?',
                           (plan.backlog_id,)).fetchone()
        if not row or row[0] != plan.project_id:
            raise ValueError('Plan story does not belong to this project')
        conn.execute('INSERT INTO workflow_plans VALUES (?,?,?,?,?,?)',
                     (plan.id, plan.project_id, plan.backlog_id, plan.revision, payload, _now()))


def get_plan(conn, identity: str) -> StoryPlan:
    return StoryPlan.model_validate(_get(conn, 'workflow_plans', identity)['payload'])


def list_plans(conn, project_id: str) -> list[StoryPlan]:
    rows = conn.execute('SELECT p.payload FROM workflow_plans p WHERE p.project_id=? AND '
                        'p.revision=(SELECT MAX(q.revision) FROM workflow_plans q '
                        'WHERE q.backlog_id=p.backlog_id) ORDER BY p.backlog_id', (project_id,))
    return [StoryPlan.model_validate_json(r[0]) for r in rows]


def create_proposal(conn, project_id: str, plans: list[StoryPlan], *, base_commit: str,
                    context_revision: str, limit: int, budget_usd: float,
                    excluded: dict | None = None) -> dict:
    if not plans or limit < 1 or budget_usd <= 0:
        raise ValueError('Proposal requires plans, capacity and budget')
    payload = {'plan_ids': [p.id for p in plans], 'base_commit': base_commit,
               'context_revision': context_revision, 'limit': limit, 'budget_usd': budget_usd,
               'excluded': excluded or {}}
    identity = _id()
    conn.execute('INSERT INTO workflow_proposals (id,project_id,payload,created_at) VALUES (?,?,?,?)',
                 (identity, project_id, _json(payload), _now()))
    return get_proposal(conn, identity)


def get_proposal(conn, identity: str) -> dict:
    return _get(conn, 'workflow_proposals', identity)


def list_proposals(conn, project_id: str) -> list[dict]:
    return _list(conn, 'workflow_proposals', project_id)


def launch_proposal(conn, identity: str) -> bool:
    """CAS only; service must revalidate plans and reserve all jobs in atomic()."""
    return conn.execute("UPDATE workflow_proposals SET status='launched' WHERE id=? "
                        "AND status='proposed'", (identity,)).rowcount == 1


def enqueue_job(conn, project_id: str, kind: str, key: str, payload: dict, *,
                run_id: int | None = None, proposal_id: str | None = None) -> dict:
    with atomic(conn):
        old = conn.execute('SELECT * FROM workflow_jobs WHERE key=?', (key,)).fetchone()
        if old:
            if old['project_id'] != project_id or old['kind'] != kind or old['payload'] != _json(payload):
                raise ValueError('Job key already identifies different work')
            return _row(old)
        identity, now = _id(), _now()
        conn.execute('INSERT INTO workflow_jobs '
                     '(id,project_id,kind,key,payload,run_id,proposal_id,created_at,updated_at) '
                     'VALUES (?,?,?,?,?,?,?,?,?)',
                     (identity, project_id, kind, key, _json(payload), run_id, proposal_id, now, now))
        return get_job(conn, identity)


def get_job(conn, identity: str) -> dict:
    return _get(conn, 'workflow_jobs', identity)


def list_jobs(conn, project_id: str) -> list[dict]:
    return _list(conn, 'workflow_jobs', project_id)


def claim_job(conn, owner: str, *, lease_seconds: int = 120) -> dict | None:
    if not owner or lease_seconds < 1:
        raise ValueError('Owner and positive lease required')
    with atomic(conn):
        expire_jobs(conn)
        row = conn.execute("SELECT j.id FROM workflow_jobs j LEFT JOIN workflow_controls c "
                           "ON j.project_id=c.project_id WHERE j.status='queued' "
                           "AND (COALESCE(c.paused,0)=0 OR j.kind IN ('resume','release')) ORDER BY j.created_at,j.id LIMIT 1").fetchone()
        if not row:
            return None
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        conn.execute("UPDATE workflow_jobs SET status='running',owner=?,lease_until=?,updated_at=? "
                     'WHERE id=?', (owner, lease, _now(), row[0]))
        return get_job(conn, row[0])


def heartbeat_job(conn, identity: str, owner: str, *, lease_seconds: int = 120) -> bool:
    if lease_seconds < 1:
        raise ValueError('Positive lease required')
    now = _now()
    lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
    return conn.execute("UPDATE workflow_jobs SET lease_until=?,updated_at=? WHERE id=? "
                        "AND owner=? AND status='running' AND lease_until>?",
                        (lease, now, identity, owner, now)).rowcount == 1


def finish_job(conn, identity: str, owner: str, *, status: str = 'completed',
               result: dict | None = None, error: str | None = None, run_id: int | None = None) -> dict:
    if status not in ('completed', 'failed', 'waiting_human', 'interrupted'):
        raise ValueError('Invalid job completion status')
    with atomic(conn):
        old = get_job(conn, identity)
        if old['status'] == status and old['owner'] == owner and old['result'] == result:
            return old
        if old['status'] != 'running' or old['owner'] != owner or old['lease_until'] <= _now():
            raise ValueError('Worker no longer owns this job; reconciliation required')
        conn.execute('UPDATE workflow_jobs SET status=?,result=?,error=?,run_id=COALESCE(?,run_id),'
                     'lease_until=NULL,updated_at=? WHERE id=?',
                     (status, _json(result) if result is not None else None, error, run_id, _now(), identity))
        return get_job(conn, identity)


def attach_run(conn, identity: str, owner: str, run_id: int) -> None:
    if conn.execute("UPDATE workflow_jobs SET run_id=?,updated_at=? WHERE id=? AND owner=? "
                    "AND status='running' AND lease_until>?",
                    (run_id, _now(), identity, owner, _now())).rowcount != 1:
        raise ValueError('Worker no longer owns this job')


def expire_jobs(conn) -> int:
    return conn.execute("UPDATE workflow_jobs SET status='interrupted',error='Worker lease lost; "
                        "reconcile workspace before retry',updated_at=? WHERE status='running' "
                        'AND lease_until<=?', (_now(), _now())).rowcount


def reconcile_job(conn, identity: str, *, result: dict, requeue: bool = False) -> dict:
    """Explicit recovery records evidence; expiration alone never repeats a write."""
    if not result:
        raise ValueError('Recovery evidence is required')
    if conn.execute("UPDATE workflow_jobs SET status=?,result=?,owner=NULL,lease_until=NULL,"
                    "updated_at=? WHERE id=? AND status='interrupted'",
                    ('queued' if requeue else 'failed', _json(result), _now(), identity)).rowcount != 1:
        raise ValueError('Only interrupted jobs may be reconciled')
    return get_job(conn, identity)


def set_paused(conn, project_id: str, paused: bool) -> None:
    conn.execute('INSERT INTO workflow_controls VALUES (?,?) ON CONFLICT(project_id) '
                 'DO UPDATE SET paused=excluded.paused', (project_id, int(paused)))


def is_paused(conn, project_id: str) -> bool:
    row = conn.execute('SELECT paused FROM workflow_controls WHERE project_id=?', (project_id,)).fetchone()
    return bool(row and row[0])


def add_event(conn, project_id: str, kind: str, message: str, details: dict | None = None) -> None:
    conn.execute('INSERT INTO workflow_events (project_id,kind,message,details,created_at) '
                 'VALUES (?,?,?,?,?)', (project_id, kind, message, _json(details or {}), _now()))


def list_events(conn, project_id: str) -> list[dict]:
    return _list(conn, 'workflow_events', project_id)


def create_budget(conn, identity: str, project_id: str, cap_usd: float) -> None:
    if not 0 < cap_usd < float('inf'):
        raise ValueError('A finite positive budget is required')
    old = conn.execute('SELECT project_id,cap_usd FROM workflow_budgets WHERE id=?', (identity,)).fetchone()
    if old:
        if tuple(old) != (project_id, cap_usd):
            raise ValueError('Budget already exists with different terms')
        return
    conn.execute('INSERT INTO workflow_budgets VALUES (?,?,?)', (identity, project_id, cap_usd))


def reserve_budget(conn, account_id: str, identity: str, amount_usd: float) -> None:
    if not 0 < amount_usd < float('inf'):
        raise ValueError('A finite positive reservation is required')
    with atomic(conn):
        old = conn.execute('SELECT account_id,amount_usd FROM workflow_reservations WHERE id=?',
                           (identity,)).fetchone()
        if old:
            if tuple(old) != (account_id, amount_usd):
                raise ValueError('Reservation already exists with different terms')
            return
        account = conn.execute('SELECT cap_usd FROM workflow_budgets WHERE id=?', (account_id,)).fetchone()
        if not account:
            raise ValueError('Unknown budget account')
        used = conn.execute('SELECT COALESCE(SUM(CASE WHEN actual_usd IS NULL THEN amount_usd '
                            'ELSE actual_usd END),0) FROM workflow_reservations WHERE account_id=?',
                            (account_id,)).fetchone()[0]
        if used + amount_usd > account[0] + 1e-9:
            raise ValueError('Budget is already reserved or spent')
        conn.execute('INSERT INTO workflow_reservations (id,account_id,amount_usd) VALUES (?,?,?)',
                     (identity, account_id, amount_usd))


def settle_budget(conn, identity: str, actual_usd: float | None) -> None:
    # Unknown usage retains its entire reservation. It must not become free money.
    if actual_usd is None:
        return
    if not 0 <= actual_usd < float('inf'):
        raise ValueError('Usage must be finite and nonnegative')
    with atomic(conn):
        row = conn.execute('SELECT actual_usd FROM workflow_reservations WHERE id=?', (identity,)).fetchone()
        if row is None or (row[0] is not None and row[0] != actual_usd):
            raise ValueError('Unknown or already settled reservation')
        conn.execute("UPDATE workflow_reservations SET actual_usd=?,status='settled' WHERE id=?",
                     (actual_usd, identity))


def acquire_lock(conn, project_id: str, resource: str, owner: str) -> bool:
    """Durable resource claim; release explicitly, never expire an uncertain writer."""
    with atomic(conn):
        row = conn.execute('SELECT owner FROM workflow_locks WHERE project_id=? AND resource=?',
                           (project_id, resource)).fetchone()
        if row:
            return row[0] == owner
        conn.execute('INSERT INTO workflow_locks VALUES (?,?,?,?)', (project_id, resource, owner, _now()))
        return True


def release_lock(conn, project_id: str, resource: str, owner: str) -> bool:
    return conn.execute('DELETE FROM workflow_locks WHERE project_id=? AND resource=? AND owner=?',
                        (project_id, resource, owner)).rowcount == 1


def save_decision_draft(conn, identity: str, text: str) -> None:
    conn.execute("UPDATE workflow_decisions SET draft_text=? WHERE id=? AND status='pending'",
                 (text, identity))


def update_proposal(conn, identity: str, status: str, result: dict | None = None) -> dict:
    old = get_proposal(conn, identity)
    payload = old['payload']
    if result is not None:
        payload = {**payload, 'result': result}
    conn.execute('UPDATE workflow_proposals SET status=?,payload=? WHERE id=?',
                 (status, _json(payload), identity))
    return get_proposal(conn, identity)


def set_run_workspace(conn, run_id: int, *, path: str, branch: str, plan_id: str,
                      batch_id: str, project_spec_snapshot: str | None) -> None:
    conn.execute('UPDATE pipeline_runs SET workspace_path=?,workspace_branch=?,plan_id=?,'
                 'batch_id=?,project_spec_snapshot=? WHERE id=?',
                 (path, branch, plan_id, batch_id, project_spec_snapshot, run_id))


def batch_runs(conn, batch_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute('SELECT * FROM pipeline_runs WHERE batch_id=? ORDER BY id',
                                        (batch_id,))]


def reserved_plans(conn, project_id: str) -> tuple[StoryPlan, ...]:
    # Claims survive parked and failed jobs; only integration/abandonment releases them.
    proposals = [p for p in list_proposals(conn, project_id)
                 if p['status'] in ('launched', 'integrating', 'review', 'blocked')]
    ids = {j['payload']['plan_id'] for j in list_jobs(conn, project_id)
           if j['kind'] == 'build' and any(p['id'] == j['proposal_id'] for p in proposals)}
    return tuple(get_plan(conn, identity) for identity in ids)


def integrated_stories(conn, project_id: str) -> frozenset[int]:
    ids = set()
    for proposal in list_proposals(conn, project_id):
        if proposal['status'] == 'integrated':
            for job in list_jobs(conn, project_id):
                if job['proposal_id'] == proposal['id'] and job['kind'] == 'build':
                    ids.add(get_plan(conn, job['payload']['plan_id']).backlog_id)
    return frozenset(ids)


def legacy_workspace_owner(conn, project_id: str, except_run: int | None = None) -> dict | None:
    """The running or parked single-checkout run that owns the product checkout, if any."""
    row = conn.execute("SELECT id, status FROM pipeline_runs WHERE project_id=? AND replay_of IS NULL "
                       "AND workspace_path IS NULL AND status IN ('running','waiting_human') "
                       "AND id != ? ORDER BY id LIMIT 1", (project_id, except_run or -1)).fetchone()
    return dict(row) if row else None


def legacy_workspace_busy(conn, project_id: str, except_run: int | None = None) -> bool:
    return legacy_workspace_owner(conn, project_id, except_run) is not None


def all_jobs(conn) -> list[dict]:
    return [_row(r) for r in conn.execute('SELECT * FROM workflow_jobs ORDER BY created_at,id')]


def request_stop(conn, identity: str) -> None:
    job = get_job(conn, identity)
    payload = {**job['payload'], 'stop_requested': True}
    conn.execute('UPDATE workflow_jobs SET payload=? WHERE id=?', (_json(payload), identity))


def abandon_proposal(conn, identity: str) -> None:
    proposal = get_proposal(conn, identity)
    if conn.execute("SELECT 1 FROM workflow_jobs WHERE proposal_id=? AND status='running'", (identity,)).fetchone():
        raise ValueError('A worker is still running this batch; stop it before abandoning')
    if proposal['status'] == 'integrated':
        raise ValueError('An integrated batch cannot be abandoned')
    conn.execute("UPDATE workflow_jobs SET status='cancelled',updated_at=? WHERE proposal_id=? "
                 "AND status IN ('queued','interrupted')", (_now(), identity))
    for run in batch_runs(conn, identity):
        if run['status'] == 'running':
            raise ValueError('A run is still marked running; reconcile it before abandoning')
    conn.execute("UPDATE workflow_proposals SET status='abandoned' WHERE id=?", (identity,))
    conn.execute("UPDATE backlog_stories SET status='approved',run_id=NULL,story_id=NULL,base_commit=NULL "
                 "WHERE run_id IN (SELECT id FROM pipeline_runs WHERE batch_id=?)", (identity,))
    # Retain runs and workspaces; they are evidence, never silently delete their contents.
    conn.execute("UPDATE pipeline_runs SET archived_from=status,status='archived' WHERE batch_id=?",
                 (identity,))
    add_event(conn, proposal['project_id'], 'batch', 'Batch abandoned; workspaces and history retained',
              {'proposal_id': identity})


def used_plans(conn) -> set[str]:
    return {r[0] for r in conn.execute('SELECT plan_id FROM pipeline_runs WHERE plan_id IS NOT NULL')}


def retry_failed_job(conn, identity: str, *, payload: dict | None = None) -> dict:
    """Queue a failed job's work again under a new key; the failed one is marked retried.

    Only for work whose failure wrote nothing it could repeat (the caller decides which).
    """
    with atomic(conn):
        job = get_job(conn, identity)
        if job['status'] != 'failed':
            raise ValueError('Only a failed job can be retried')
        conn.execute("UPDATE workflow_jobs SET status='retried',updated_at=? WHERE id=?",
                     (_now(), identity))
        return enqueue_job(conn, job['project_id'], job['kind'],
                           f"{job['key']}:retry:{_id()}", payload or job['payload'])


def dismiss_failed_job(conn, identity: str) -> dict:
    """Close a failed or interrupted job without repeating anything."""
    with atomic(conn):
        job = get_job(conn, identity)
        if job['status'] not in ('failed', 'interrupted'):
            raise ValueError('Only a failed or stopped job can be dismissed')
        conn.execute("UPDATE workflow_jobs SET status='dismissed',updated_at=? WHERE id=?",
                     (_now(), identity))
        return get_job(conn, identity)


def recover_job(conn, identity: str) -> dict:
    """Settle a lost worker from recorded run state; never repeat an unknown write."""
    with atomic(conn):
        job = get_job(conn, identity)
        if job['status'] not in ('interrupted', 'failed'):
            raise ValueError('Only interrupted or failed jobs need recovery')
        if job['run_id']:
            run = conn.execute('SELECT * FROM pipeline_runs WHERE id=?', (job['run_id'],)).fetchone()
            if run['status'] == 'running':
                conn.execute("UPDATE pipeline_runs SET status='blocked',error=? WHERE id=?",
                             ('Worker lost; inspect retained workspace before retrying', run['id']))
            conn.execute("UPDATE workflow_jobs SET status='failed',updated_at=? WHERE id=?", (_now(), identity))
        else:
            conn.execute("UPDATE workflow_jobs SET status='failed',updated_at=? WHERE id=?", (_now(), identity))
        add_event(conn, job['project_id'], 'recovery', 'Lost job settled; no operation repeated', {'job_id': identity})
        return get_job(conn, identity)
