"""Decision-oriented board read model and small operator controls."""
from __future__ import annotations

from pathlib import Path

from factory.runs.events import RunError
from factory.runs.worker import worker_status
from factory.state import workflow as store
from factory.state.backlog import list_backlog
from factory.state.db import get_db, get_run, get_runs_by_status, init_db
from factory.workspace.projects import get_project, list_projects


def dashboard(project_ref: str | None, *, db_path: Path) -> dict:
    init_db(db_path)
    projects = list_projects(db_path)
    selected = [get_project(db_path, project_ref)] if project_ref else projects
    data = {'projects': projects, 'worker': worker_status(db_path=db_path),
            'decisions': [], 'stories': [], 'jobs': [], 'proposals': [], 'events': [],
            'runs': [], 'paused': False, 'plans': {}}
    with get_db(db_path) as conn:
        for project in selected:
            pid = project['id']
            sessions = store.list_sessions(conn, pid)
            plans = store.list_plans(conn, pid)
            data['plans'].update({p.id: p.model_dump() for p in plans})
            data['decisions'] += [{**d, 'project': project['slug']} for d in store.list_decisions(conn, pid)
                                  if d['status'] == 'pending']
            data['jobs'] += [{**j, 'project': project['slug']} for j in store.list_jobs(conn, pid)]
            data['proposals'] += [{**p, 'project': project['slug']} for p in store.list_proposals(conn, pid)
                                  if p['status'] not in ('integrated', 'abandoned')]
            data['events'] += [{**e, 'project': project['slug']} for e in store.list_events(conn, pid)]
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
        ids = {p['id'] for p in selected}
        data['runs'] = [r for r in get_runs_by_status(conn, ['running', 'waiting_human', 'failed', 'blocked'])
                        if r['project_id'] in ids]
    data['events'].sort(key=lambda r: r['created_at'], reverse=True)
    return data


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
