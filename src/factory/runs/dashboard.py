"""Decision-oriented board read model and small operator controls."""
from __future__ import annotations

from pathlib import Path

from factory.evidence.brief import load_brief
from factory.runs.events import RunError
from factory.runs.refinement import RETRYABLE_JOBS, agreement_complete
from factory.runs.worker import worker_status
from factory.state import workflow as store
from factory.state.backlog import list_backlog
from factory.state.db import get_db, get_run, get_runs_by_status, init_db
from factory.state.interviews import list_answers
from factory.workspace.projects import get_project, list_projects


def dashboard(project_ref: str | None, *, db_path: Path) -> dict:
    init_db(db_path)
    projects = list_projects(db_path)
    selected = [get_project(db_path, project_ref)] if project_ref else projects
    data = {'projects': projects, 'worker': worker_status(db_path=db_path),
            'decisions': [], 'stories': [], 'jobs': [], 'proposals': [], 'events': [],
            'runs': [], 'paused': False, 'plans': {}, 'project': None}
    with get_db(db_path) as conn:
        for project in selected:
            pid = project['id']
            sessions = store.list_sessions(conn, pid)
            plans = store.list_plans(conn, pid)
            data['plans'].update({p.id: p.model_dump() for p in plans})
            data['decisions'] += _described(
                [d for d in store.list_decisions(conn, pid) if d['status'] == 'pending'],
                project['slug'], sessions, {r['id']: r for r in list_backlog(conn, pid)})
            jobs = [_job(j, project['slug'], sessions) for j in store.list_jobs(conn, pid)]
            data['jobs'] += jobs
            data['proposals'] += [{**p, 'project': project['slug']} for p in store.list_proposals(conn, pid)
                                  if p['status'] not in ('integrated', 'abandoned')]
            decided = {d['id']: d for d in store.list_decisions(conn, pid)}
            data['events'] += [_event(e, project['slug'], decided) for e in store.list_events(conn, pid)]
            data['paused'] |= store.is_paused(conn, pid)
            for row in list_backlog(conn, pid):
                session = next((s for s in sessions if s['backlog_id'] == row['id']), None)
                run = get_run(conn, row['run_id']) if row['run_id'] else None
                plan = next((p for p in plans if p.backlog_id == row['id']), None)
                state = 'Draft'
                if session:
                    state = {'ready': 'Ready', 'needs_input': 'Needs input', 'blocked': 'Blocked',
                             'refining': 'Refining'}.get(session['status'], session['status'])
                if plan and any(j['payload'].get('plan_id') == plan.id and j['status'] == 'queued'
                                for j in data['jobs']):
                    state = 'Scheduled'
                if run:
                    state = {'running': 'Working', 'waiting_human': 'Needs input',
                             'failed': 'Blocked', 'blocked': 'Blocked', 'completed': 'Done',
                             'archived': 'Archived'}.get(run['status'], run['status'])
                    if run.get('batch_id') and run['status'] in ('completed', 'waiting_human'):
                        batch = store.get_proposal(conn, run['batch_id'])
                        if batch['status'] != 'integrated' and (run['status'] == 'completed'
                                or run['current_stage'] == 'gate-release-human'):
                            state = 'Awaiting integration'
                data['stories'].append({**row, 'project': project['slug'], 'state': state,
                                        'session': session, 'plan': plan.model_dump() if plan else None,
                                        'run': run})
        if project_ref:
            data['project'] = _project_state(conn, selected[0], sessions, data)
        ids = {p['id'] for p in selected}
        data['runs'] = [r for r in get_runs_by_status(conn, ['running', 'waiting_human', 'failed', 'blocked'])
                        if r['project_id'] in ids]
    data['events'].sort(key=lambda r: r['created_at'], reverse=True)
    return data


def _project_state(conn, project: dict, sessions: list[dict], data: dict) -> dict:
    """What the project-level actions depend on (`interfaces.board.views.project_actions`)."""
    pid = project['id']
    open_jobs = [j for j in data['jobs'] if j['project_id'] == pid and j['status'] in ('queued', 'running')]
    intake = next((s for s in sessions if s['backlog_id'] is None), None)
    return {
        'slug': project['slug'],
        'brief': bool(load_brief(Path(project['repo_path']))),
        'agreement': agreement_complete(list_answers(conn, pid)),
        'intake_open': bool(intake and (intake['status'] in ('refining', 'needs_input') or any(
            j['payload'].get('session_id') == intake['id'] for j in open_jobs))),
        'backlog_open': any(j['kind'] == 'backlog' for j in open_jobs) or any(
            d['kind'] == 'backlog' and d['project_id'] == pid for d in data['decisions']),
        'ready': sum(s['state'] == 'Ready' and s['project_id'] == pid for s in data['stories']),
        'stories': sum(s['project_id'] == pid for s in data['stories']),
        'batch_open': any(p['status'] == 'proposed' and p['project_id'] == pid
                          for p in data['proposals']),
        'paused': data['paused'],
        'queued': len(open_jobs),
    }


def _job(job: dict, slug: str, sessions: list[dict]) -> dict:
    """A job with what it was doing in words (`subject`) and whether Retry is safe."""
    session = next((s for s in sessions if s['id'] == job['payload'].get('session_id')), None)
    if job['kind'] == 'backlog':
        subject = 'Backlog proposal'
    elif session and session['backlog_id'] is not None:
        subject = f"Story #{session['backlog_id']} refinement"
    elif session:
        subject = 'Product interview'
    else:
        subject = job['kind'].capitalize()
    return {**job, 'project': slug, 'subject': subject,
            'retryable': job['status'] == 'failed' and job['kind'] in RETRYABLE_JOBS}


def _event(event: dict, slug: str, decisions: dict[str, dict]) -> dict:
    """An event with the question and answer it refers to, when it names a decision."""
    details = dict(event['details'] or {})
    if (decision := decisions.get(details.get('decision_id'))) is not None:
        details.setdefault('question', decision['question'])
        if decision.get('answer'):
            details.setdefault('answer', decision['answer'])
    return {**event, 'project': slug, 'details': details}


def _described(pending: list[dict], slug: str, sessions: list[dict],
               rows: dict[int, dict]) -> list[dict]:
    """Each decision with what it is about (`subject`) and, for one of several questions
    from the same refinement, which one it is (`position`: (n, of))."""
    by_session = {s['id']: s for s in sessions}
    described = []
    for d in pending:
        session = by_session.get(d['session_id'])
        subject = None
        if session and session['backlog_id'] is not None:
            row = rows.get(session['backlog_id'])
            subject = f"Story #{row['id']} {row['title']}" if row else f"Story #{session['backlog_id']}"
        elif session:
            subject = 'Product interview'
        siblings = [x['id'] for x in pending if d['session_id'] and x['session_id'] == d['session_id']]
        position = (siblings.index(d['id']) + 1, len(siblings)) if len(siblings) > 1 else None
        described.append({**d, 'project': slug, 'subject': subject, 'position': position})
    return described


def pause_project(project_ref: str, paused: bool, *, db_path: Path) -> None:
    project = get_project(db_path, project_ref)
    with get_db(db_path) as conn:
        store.set_paused(conn, project['id'], paused)
        store.add_event(conn, project['id'], 'control',
                        'New starts paused' if paused else 'New starts resumed')


def stop_job(job_id: str, *, db_path: Path) -> None:
    with get_db(db_path) as conn:
        job = store.get_job(conn, job_id)
        if job['status'] not in ('queued', 'running'):
            raise RunError('Only queued or running work can be stopped')
        store.request_stop(conn, job_id)
        store.add_event(conn, job['project_id'], 'control', 'Stop requested at next safe boundary',
                        {'job_id': job_id})


def recover_job(job_id: str, *, db_path: Path) -> None:
    if worker_status(db_path=db_path) == 'active':
        raise RunError('Stop the detached worker before reconciling uncertain work')
    with get_db(db_path) as conn:
        store.expire_jobs(conn)
        store.recover_job(conn, job_id)
