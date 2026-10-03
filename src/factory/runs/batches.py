"""Reviewable batch launch, isolated runs, and serialized integration/release."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from factory.agent_config.settings import settings
from factory.domain.workflow import assess_batch
from factory.runs.context import load_project_spec_text
from factory.runs.events import RunError, RunStarted
from factory.runs.refinement import context_revision
from factory.runs.service import resume_run, run_pipeline
from factory.state import backlog, workflow as store
from factory.state.db import get_db, get_pending_human_gate, get_run, init_db
from factory.verification.integration import verify_candidate
from factory.workspace.git import git_changed_files, git_commit_paths, git_head
from factory.workspace.layout import EVIDENCE_PATHS, resolve_location, store_location
from factory.workspace.projects import get_project
from factory.workspace.worktrees import clean, create_workspace, git, merge_candidate, project_lock


def propose_batch(project_ref: str, *, db_path: Path, limit: int = 2,
                  budget_usd: float | None = None) -> dict:
    init_db(db_path)
    project = get_project(db_path, project_ref)
    base = git_head(Path(project['repo_path'])) or ''
    context = context_revision(project)
    with get_db(db_path) as conn, store.atomic(conn):
        if store.legacy_workspace_busy(conn, project['id']):
            raise RunError('A legacy run owns this checkout. Refinement can continue; launch waits.')
        rows = {r['id']: r for r in backlog.list_backlog(conn, project['id'])}
        used = store.used_plans(conn)
        plans = [p for p in store.list_plans(conn, project['id'])
                 if rows.get(p.backlog_id, {}).get('status') == 'approved' and p.id not in used]
        assessment = assess_batch(plans, context_revision=context, base_commit=base,
                                  completed=store.integrated_stories(conn, project['id']),
                                  active=store.reserved_plans(conn, project['id']), limit=limit)
        if not assessment.selected:
            reasons = json.dumps(assessment.excluded, indent=2) if assessment.excluded else 'Refine stories first.'
            raise RunError('No compatible ready stories. ' + reasons)
        selected = list(assessment.selected)
        budget = budget_usd if budget_usd is not None else sum(p.budget_usd for p in selected)
        if sum(p.budget_usd for p in selected) > budget:
            raise RunError('Batch budget must cover each selected story allowance')
        proposal = store.create_proposal(conn, project['id'], selected, base_commit=base,
                                         context_revision=context, limit=limit, budget_usd=budget,
                                         excluded=assessment.excluded)
        store.add_event(conn, project['id'], 'batch', 'Compatible batch proposed',
                        {'proposal_id': proposal['id']})
        return proposal


def launch_batch(proposal_id: str, *, db_path: Path, selected_ids: list[int] | None = None) -> dict:
    with get_db(db_path) as conn:
        proposal = store.get_proposal(conn, proposal_id)
    project = get_project(db_path, proposal['project_id'])
    with project_lock(db_path, project['id']), get_db(db_path) as conn, store.atomic(conn):
        proposal = store.get_proposal(conn, proposal_id)
        if proposal['status'] != 'proposed':
            return proposal  # repeat clicks never launch a second time
        if store.is_paused(conn, project['id']):
            raise RunError('New starts are paused for this project')
        if store.legacy_workspace_busy(conn, project['id']):
            raise RunError('A legacy run still owns the project checkout')
        if not clean(Path(project['repo_path'])):
            raise RunError('Project checkout must be clean before launching an isolated batch')
        payload = proposal['payload']
        plans = [store.get_plan(conn, identity) for identity in payload['plan_ids']]
        if selected_ids is not None:
            if not selected_ids or set(selected_ids) - {p.backlog_id for p in plans}:
                raise RunError('Select only stories from this proposal')
            plans = [p for p in plans if p.backlog_id in selected_ids]
        latest = {p.backlog_id: p.id for p in store.list_plans(conn, project['id'])}
        if any(latest.get(p.backlog_id) != p.id for p in plans):
            raise RunError('A plan was revised; propose a new batch')
        assessment = assess_batch(plans, context_revision=context_revision(project),
                                  base_commit=git_head(Path(project['repo_path'])) or '',
                                  completed=store.integrated_stories(conn, project['id']),
                                  active=store.reserved_plans(conn, project['id']), limit=payload['limit'])
        if assessment.excluded:
            raise RunError('Proposal is no longer launchable: ' + json.dumps(assessment.excluded))
        store.create_budget(conn, proposal_id, project['id'], payload['budget_usd'])
        for plan in plans:
            row = backlog.get_backlog_row(conn, plan.backlog_id)
            if not row or row['status'] != 'approved':
                raise RunError('A selected story was already started')
            store.reserve_budget(conn, proposal_id, plan.id, plan.budget_usd)
            store.enqueue_job(conn, project['id'], 'build', f'build:{plan.id}',
                              {'plan_id': plan.id}, proposal_id=proposal_id)
        store.launch_proposal(conn, proposal_id)
        store.add_event(conn, project['id'], 'batch', 'Selected batch queued',
                        {'proposal_id': proposal_id, 'stories': [p.backlog_id for p in plans]})
        return store.get_proposal(conn, proposal_id)


def queue_resume(run_id: int, action: str, reason: str | None = None, *, db_path: Path) -> dict:
    if action not in ('approve', 'reject') or (action == 'reject' and not (reason or '').strip()):
        raise RunError('Choose approve, or reject with feedback')
    with get_db(db_path) as conn, store.atomic(conn):
        run = get_run(conn, run_id)
        pending = get_pending_human_gate(conn, run_id)
        if not run or run['status'] != 'waiting_human' or not pending:
            raise RunError('This run is no longer waiting for a decision')
        return store.enqueue_job(conn, run['project_id'], 'resume', f"gate:{pending['id']}",
                                  {'action': action, 'reason': reason}, run_id=run_id,
                                  proposal_id=run.get('batch_id'))


def queue_integration(proposal_id: str, *, db_path: Path) -> dict:
    with get_db(db_path) as conn, store.atomic(conn):
        proposal = store.get_proposal(conn, proposal_id)
        if proposal['status'] not in ('launched', 'blocked'):
            raise RunError('Only a launched batch can be integrated')
        if any(j['proposal_id'] == proposal_id and j['status'] in ('queued', 'running', 'interrupted')
               for j in store.list_jobs(conn, proposal['project_id'])):
            raise RunError('Wait for batch workers to park, or reconcile interrupted jobs first')
        runs = store.batch_runs(conn, proposal_id)
        builds = [j for j in store.list_jobs(conn, proposal['project_id'])
                  if j['kind'] == 'build' and j['proposal_id'] == proposal_id]
        if not runs or len(runs) != len(builds):
            raise RunError('Some stories have not produced candidates yet')
        for run in runs:
            gate = get_pending_human_gate(conn, run['id'])
            if run['status'] != 'completed' and not (run['status'] == 'waiting_human' and gate
                    and gate['gate_name'] == 'gate-release' and gate['passed']):
                raise RunError(f"Run #{run['id']} must pass its release evidence checks first")
        store.update_proposal(conn, proposal_id, 'integrating')
        previous = [j for j in store.list_jobs(conn, proposal['project_id'])
                    if j['kind'] == 'integrate' and j['proposal_id'] == proposal_id]
        return store.enqueue_job(conn, proposal['project_id'], 'integrate',
                                  f'integrate:{proposal_id}:{len(previous)}', {}, proposal_id=proposal_id)


def _build(job: dict, db_path: Path, on_event) -> dict:
    with get_db(db_path) as conn:
        plan = store.get_plan(conn, job['payload']['plan_id'])
        proposal = store.get_proposal(conn, job['proposal_id'])
    project = get_project(db_path, job['project_id'])
    if proposal['status'] != 'launched' or context_revision(project) != plan.context_revision:
        raise RunError('Batch/context changed before this start; prepare a new proposal')
    target = db_path.parent / 'worktrees' / f"story-{job['id']}"
    branch = f"factory/story-{job['id']}"
    with project_lock(db_path, project['id']):
        if git_head(Path(project['repo_path'])) != plan.base_commit:
            raise RunError('Baseline changed before this story started')
        project_spec_text = plan.context.get("spec") or load_project_spec_text(project)
        create_workspace(Path(project['repo_path']), target, branch, plan.base_commit)

    def event(value) -> None:
        if isinstance(value, RunStarted):
            with get_db(db_path) as conn:
                store.attach_run(conn, job['id'], job['owner'], value.run_id)
                backlog.mark_started(conn, plan.backlog_id, story_id=value.story_id, run_id=value.run_id)
        if on_event:
            on_event(value)
    outcome = run_pipeline(plan.request, opencode_cwd=str(target),
                           project_spec_text=project_spec_text,
                           db_path=db_path, project_id=project['id'], base_commit=plan.base_commit,
                           workspace={'branch': branch, 'plan_id': plan.id, 'batch_id': proposal['id']},
                           on_event=event)
    return {'run_id': outcome.run_id, 'status': outcome.status, 'error': outcome.error}


def _integrate(job: dict, db_path: Path) -> dict:
    with get_db(db_path) as conn:
        proposal = store.get_proposal(conn, job['proposal_id'])
        runs = store.batch_runs(conn, proposal['id'])
    project = get_project(db_path, job['project_id'])
    repo = Path(project['repo_path'])
    root = db_path.parent / 'worktrees' / f"integration-{job['id']}"
    baseline = git_head(repo)
    try:
        with project_lock(db_path, project['id']):
            if baseline != proposal['payload']['base_commit']:
                raise RunError('Integration baseline changed; reassess before integrating')
            create_workspace(repo, root, f"factory/integration-{job['id']}", baseline)
            members = []
            for run in runs:
                path = resolve_location(run['workspace_path'], db_path)
                if path is None or not clean(path):
                    raise RunError(f"Run #{run['id']} workspace is missing or dirty")
                head = git_head(path)
                if not run.get('candidate_commit'):
                    raise RunError('Missing pinned story candidate')
                changed = git_changed_files(path, run['candidate_commit'], end=head, exclude=EVIDENCE_PATHS)
                if changed is None or changed:
                    raise RunError('Story changed after review; its candidate must be re-reviewed')
                merge_candidate(root, head)
                members.append({'run_id': run['id'], 'head': head, 'candidate': run['candidate_commit']})
            manifest = root / 'docs/releases' / f"batch-{proposal['id']}.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({'batch': proposal['id'], 'baseline': baseline,
                                           'members': members}, indent=2) + '\n')
            if not git_commit_paths(root, [manifest], 'factory: combined candidate manifest'):
                raise RunError('Could not commit integration evidence')
            candidate = git_head(root)
        paths = git_changed_files(root, baseline, end=candidate, exclude=EVIDENCE_PATHS)
        if paths is None:
            raise RunError('Cannot measure combined candidate')
        checks = verify_candidate(root, [p['path'] for p in paths])
        if not clean(root) or git_head(root) != candidate:
            raise RunError('Verification changed candidate files; reconcile before review')
        result = {'status': 'review' if checks.passed else 'blocked', 'candidate': candidate,
                  'baseline': baseline, 'workspace': store_location(root, db_path),
                  'members': members, 'checks': [asdict(c) for c in checks.checks]}
        with get_db(db_path) as conn:
            store.update_proposal(conn, proposal['id'], result['status'], result)
            if checks.passed:
                store.add_decision(conn, project['id'], f"release:{proposal['id']}:{candidate}",
                                   'Review and integrate the combined candidate', kind='release',
                                   context={**result, 'proposal_id': proposal['id'],
                                            'effect': 'Approve this exact revision for integration. Deployment is separate.'})
        return result
    except Exception:
        with get_db(db_path) as conn:
            store.update_proposal(conn, proposal['id'], 'blocked')
        raise


def approve_integration(decision_id: str, *, db_path: Path) -> dict:
    with get_db(db_path) as conn:
        decision = store.get_decision(conn, decision_id)
        if decision['kind'] != 'release':
            raise RunError('This is not a combined release decision')
        proposal = store.get_proposal(conn, decision['context']['proposal_id'])
    project = get_project(db_path, proposal['project_id'])
    context = decision['context']
    repo = Path(project['repo_path'])
    root = resolve_location(context['workspace'], db_path)
    with project_lock(db_path, project['id']):
        if proposal['status'] == 'integrated':
            return proposal
        if proposal['status'] != 'review' or root is None:
            raise RunError('This candidate is no longer awaiting review')
        if git_head(root) != context['candidate'] or not clean(root):
            raise RunError('Combined candidate changed; previous verification is stale')
        prior_approval = decision['status'] == 'answered' and decision['answer'] == f"Approved {context['candidate']}"
        current = git_head(repo)
        if (current != context['baseline'] and not (prior_approval and current == context['candidate'])) or not clean(repo):
            raise RunError('Product changed; combined verification must be repeated')
        for member in context['members']:
            with get_db(db_path) as conn:
                run = get_run(conn, member['run_id'])
            path = resolve_location(run['workspace_path'], db_path)
            changed = git_changed_files(path, member['head'], end=git_head(path), exclude=EVIDENCE_PATHS)
            if changed is None or changed or not clean(path):
                raise RunError('A story candidate changed after integration checks')
        with get_db(db_path) as conn:
            store.answer_decision(conn, decision_id, f"Approved {context['candidate']}")
        # This durable approval makes recovery after a partial integration idempotent.
        # The operator's combined approval covers each included story, unchanged.
        for member in context['members']:
            with get_db(db_path) as conn:
                run = get_run(conn, member['run_id'])
            if run['status'] == 'waiting_human':
                outcome = resume_run(run['id'], 'approve',
                                     reason=f"Combined candidate {context['candidate']} approved",
                                     db_path=db_path, combined_candidate=context['candidate'])
                if outcome.status != 'completed':
                    raise RunError(f"Run #{run['id']} could not accept release approval: {outcome.status}")
        git(repo, 'merge', '--ff-only', context['candidate'])
        with get_db(db_path) as conn, store.atomic(conn):
            store.answer_decision(conn, decision_id, f"Approved {context['candidate']}")
            result = store.update_proposal(conn, proposal['id'], 'integrated')
            store.add_event(conn, project['id'], 'integration', 'Combined candidate integrated; not deployed', context)
            return result


def dispatch_job(job: dict, *, db_path: Path, on_event=None) -> dict[str, Any]:
    if job['kind'] == 'build':
        return _build(job, db_path, on_event)
    if job['kind'] == 'resume':
        outcome = resume_run(job['run_id'], job['payload']['action'], job['payload'].get('reason'),
                             db_path=db_path, on_event=on_event)
        return {'run_id': outcome.run_id, 'status': outcome.status, 'error': outcome.error}
    if job['kind'] == 'integrate':
        return _integrate(job, db_path)
    if job['kind'] == 'release':
        result = approve_integration(job['payload']['decision_id'], db_path=db_path)
        return {'status': result['status']}
    raise RunError(f"Unknown job kind: {job['kind']}")


def abandon_batch(proposal_id: str, *, db_path: Path) -> None:
    with get_db(db_path) as conn, store.atomic(conn):
        store.abandon_proposal(conn, proposal_id)


def queue_release(decision_id: str, *, db_path: Path) -> dict:
    with get_db(db_path) as conn, store.atomic(conn):
        decision = store.get_decision(conn, decision_id)
        if decision['kind'] != 'release':
            raise RunError('Select a combined candidate release decision')
        return store.enqueue_job(conn, decision['project_id'], 'release', f'release:{decision_id}',
                                  {'decision_id': decision_id},
                                  proposal_id=decision['context']['proposal_id'])
