"""Durable intake: a job asks questions then releases its worker until answers arrive."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from factory.agent_config.settings import settings
from factory.domain.interview import (
    InterviewQuestion, InterviewTurn, StoryClarification, fallback_questions,
    is_undecided, resolve_answer, uncovered_topics, with_clarifications,
)
from factory.domain.project_spec import ProjectSpec
from factory.domain.contracts import SpecOutput, ArchitectOutput
from factory.domain.workflow import design_conflicts
from factory.domain.gates import gate_after_spec
from factory.domain.workflow import ResourceClaim, StoryPlan
from factory.evidence.brief import load_brief, render_brief, write_brief
from factory.runs.context import load_project_spec_text
from factory.runs.events import RunError
from factory.runs.interview import _ask_agent, build_stack_prompt, build_story_prompt, next_turn
from factory.state import workflow as store
from factory.state.backlog import get_backlog_row, list_backlog
from factory.state.db import get_db, init_db
from factory.state.interviews import add_answer, list_answers
from factory.state.projects import set_spec_path
from factory.workspace.git import git_commit_paths, git_head
from factory.workspace.layout import store_location
from factory.workspace.projects import get_project
from factory.workspace.repo_map import build_repo_inventory
from factory.workspace.templates import write_project_spec
from factory.workspace.worktrees import clean, project_lock


class ImpactProposal(BaseModel):
    spec: SpecOutput
    architecture: ArchitectOutput
    resources: list[ResourceClaim] = Field(default_factory=list)
    dependencies: list[int] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    rationale: str


def project_context(project: dict) -> dict:
    repo = Path(project['repo_path'])
    rules = repo / 'PROJECT_RULES.md'
    return {'brief': load_brief(repo) or '', 'spec': load_project_spec_text(project) or '',
            'rules': rules.read_text() if rules.is_file() else ''}


def context_revision(project: dict) -> str:
    return hashlib.sha256(json.dumps(project_context(project), sort_keys=True).encode()).hexdigest()


context_snapshot = context_revision


def _enqueue(conn, session: dict) -> dict:
    return store.enqueue_job(conn, session['project_id'], 'refine',
                             f"refine:{session['id']}:{session['revision']}",
                             {'session_id': session['id']})


def start_refinement(project_ref: str, backlog_id: int | None = None, *, db_path: Path) -> dict:
    init_db(db_path)
    project = get_project(db_path, project_ref)
    with get_db(db_path) as conn, store.atomic(conn):
        sessions = store.list_sessions(conn, project['id'])
        session = next((s for s in sessions if s['backlog_id'] == backlog_id
                        and s['status'] != 'cancelled'), None)
        if session and session['status'] in ('refining', 'needs_input'):
            jobs = [j for j in store.list_jobs(conn, project['id'])
                    if j['payload'].get('session_id') == session['id']]
            if session['status'] == 'needs_input' or any(j['status'] in ('queued', 'running') for j in jobs):
                return session
        row = get_backlog_row(conn, backlog_id) if backlog_id else None
        if backlog_id and (not row or row['project_id'] != project['id']
                           or row['status'] != 'approved'):
            raise RunError('Only an unstarted story in this project can be refined')
        draft = {'request': row['request'] if row else '', 'answers': [],
                 'context': project_context(project), 'context_revision': context_revision(project),
                 'base_commit': git_head(Path(project['repo_path'])), 'turns': 0}
        if session:
            session = store.update_session(conn, session['id'], phase='story' if row else 'product',
                                           draft=draft)
        else:
            session = store.create_session(conn, project['id'], backlog_id,
                                           phase='story' if row else 'product', draft=draft)
        _enqueue(conn, session)
        store.add_event(conn, project['id'], 'refinement', 'Refinement queued',
                        {'session_id': session['id'], 'backlog_id': backlog_id})
        return session


def save_draft(decision_id: str, draft: str, *, db_path: Path) -> None:
    with get_db(db_path) as conn:
        store.save_decision_draft(conn, decision_id, draft)


def answer(decision_id: str, text: str, *, db_path: Path) -> dict:
    with get_db(db_path) as conn:
        initial = store.get_decision(conn, decision_id)
    if initial["kind"] == "backlog":
        return answer_backlog(decision_id, text, db_path=db_path)
    with get_db(db_path) as conn, store.atomic(conn):
        decision = store.get_decision(conn, decision_id)
        if decision['status'] == 'answered':
            return store.answer_decision(conn, decision_id, text)
        if is_undecided(text):
            store.save_decision_draft(conn, decision_id, text)
            raise RunError('This remains an open decision. Choose an option, explain your choice, '
                           'or explicitly say "you decide" to delegate it.')
        if decision['kind'] == 'release':
            raise RunError('Use the candidate release action for a revision-bound approval')
        if not decision['session_id']:
            raise RunError('Use the action shown for this decision')
        session = store.get_session(conn, decision['session_id'])
        draft = dict(session['draft'])
        if decision['kind'] == 'brief':
            if text.strip().lower() in ('approve', 'yes'):
                phase = 'publish'
            else:
                add_answer(conn, session['project_id'], topic='correction',
                           question='Correction to the proposed brief and technical approach',
                           options=[], answer=text, assumed=False)
                draft['technical_complete'] = False
                phase = 'technical'
        else:
            question = InterviewQuestion.model_validate(decision['context']['question'])
            resolved, assumed = resolve_answer(question, text)
            if not resolved:
                raise RunError('An answer is required')
            saved = {'topic': question.topic, 'question': question.question,
                     'answer': resolved, 'assumed': assumed,
                     'options': [o.label for o in question.options]}
            draft['answers'] = [*draft.get('answers', []), saved]
            if session['backlog_id'] is None:
                add_answer(conn, session['project_id'], **saved)
            phase = session['phase']
        result = store.answer_decision(conn, decision_id, text)
        remaining = [d for d in store.list_decisions(conn, session['project_id'])
                     if d['session_id'] == session['id'] and d['status'] == 'pending']
        session = store.update_session(conn, session['id'], phase=phase, draft=draft,
                                       status='needs_input' if remaining else 'refining')
        if not remaining:
            _enqueue(conn, session)
        return result


def _questions(conn, session: dict, questions: list[InterviewQuestion], phase: str) -> dict:
    for index, question in enumerate(questions[:4]):
        store.add_decision(conn, session['project_id'],
                           f"{session['id']}:{session['revision']}:{index}", question.question,
                           session_id=session['id'], context={
                               'question': question.model_dump(), 'phase': phase,
                               'why': 'A material choice is not settled by the saved interview.',
                               'effect': 'Only this refinement waits; unrelated work can continue.'})
    return store.update_session(conn, session['id'], phase=phase,
                                draft=session['draft'], status='needs_input')


def advance_refinement(job: dict, *, db_path: Path) -> dict:
    with get_db(db_path) as conn:
        session = store.get_session(conn, job['payload']['session_id'])
        pending = [d for d in store.list_decisions(conn, session['project_id'])
                   if d['session_id'] == session['id'] and d['status'] == 'pending']
        answers = list_answers(conn, session['project_id'])
    if pending or session['status'] == 'ready':
        return {'status': session['status']}
    project = get_project(db_path, session['project_id'])
    draft = dict(session['draft'])
    repo = Path(project['repo_path'])
    if session['phase'] == 'publish':
        with project_lock(db_path, project['id']):
            with get_db(db_path) as conn:
                if store.legacy_workspace_busy(conn, project['id']):
                    raise RunError('Product publication waits for the legacy checkout run to stop')
            if not clean(repo):
                raise RunError('Product checkout has changes; publication needs a clean checkout')
            written = write_brief(repo, project.get('name') or project['id'], answers)
            spec_path = repo / 'project-spec.json'
            written.append(write_project_spec(spec_path, ProjectSpec.model_validate(draft['spec'])))
            if not git_commit_paths(repo, written, 'factory: approved product and technical interview'):
                if not clean(repo):
                    raise RunError('Could not commit the approved interview; reconcile publication')
            with get_db(db_path) as conn:
                set_spec_path(conn, project['id'], store_location(spec_path, db_path))
                store.update_session(conn, session['id'], phase='complete', draft=draft, status='ready')
                store.add_event(conn, project['id'], 'intake', 'Product and technical agreement saved')
        return {'status': 'ready'}
    if draft.get('turns', 0) >= 12:
        raise RunError('Refinement reached its bounded call limit. Review saved answers and refine again.')
    draft['turns'] = draft.get('turns', 0) + 1
    session['draft'] = draft
    if session['backlog_id'] is not None:
        if not draft['context']['brief']:
            raise RunError('Approve the product interview before refining stories')
        if context_revision(project) != draft['context_revision'] or git_head(repo) != draft['base_commit']:
            raise RunError('Project context changed during refinement; refine again against the new baseline')
        settled = [StoryClarification(a['question'], a['answer'], a['assumed'])
                   for a in draft['answers']]
        request = with_clarifications(draft['request'], settled)
        turn = _ask_agent(project, build_story_prompt(project, draft['context']['brief'],
                          draft['request'], settled), InterviewTurn, db_path=db_path,
                          resume='The refinement and answers are saved.')
        if turn.questions and not turn.done:
            with get_db(db_path) as conn:
                _questions(conn, session, turn.questions, 'story')
            return {'status': 'needs_input'}
        with get_db(db_path) as conn:
            backlog = list_backlog(conn, project['id'])
        prompt = '# Mode: impact\n' + json.dumps({
            'request': request, 'context': draft['context'],
            'inventory': build_repo_inventory(repo),
            'backlog': [{'id': r['id'], 'request': r['request'], 'status': r['status']} for r in backlog],
            'schema': ImpactProposal.model_json_schema(),
        }, indent=2)
        impact = _ask_agent(project, prompt, ImpactProposal, db_path=db_path,
                             resume='Refinement is saved; no implementation has started.')
        spec = impact.spec
        readiness = gate_after_spec(spec)
        problems = list(impact.uncertainties)
        if not readiness.passed:
            problems.append(readiness.reason)
        with get_db(db_path) as conn, store.atomic(conn):
            old = [p for p in store.list_plans(conn, project['id'])
                   if p.backlog_id == session['backlog_id']]
            plan = StoryPlan(id=uuid.uuid4().hex, project_id=project['id'],
                             backlog_id=session['backlog_id'], revision=old[0].revision + 1 if old else 1,
                             context_revision=draft['context_revision'], base_commit=draft['base_commit'] or '',
                             request=request, resources=tuple(impact.resources),
                             dependencies=tuple(impact.dependencies), uncertainties=tuple(problems),
                             rationale=impact.rationale, spec=spec.model_dump(),
                             architecture=impact.architecture.model_dump(), context=draft["context"],
                             ready=not problems,
                             budget_usd=settings().budget.max_story_cost_usd)
            conflicts = design_conflicts(plan, impact.architecture.modules_affected,
                                         api=impact.architecture.api_impact.lower() not in ("no", "none"),
                                         data=impact.architecture.db_impact.lower() not in ("no", "none"),
                                         dependencies=bool(impact.architecture.external_dependencies))
            if conflicts:
                plan = plan.model_copy(update={"uncertainties": (*plan.uncertainties, *conflicts), "ready": False})
            store.save_plan(conn, plan)
            draft['plan_id'] = plan.id
            store.update_session(conn, session['id'], phase='planned', draft=draft,
                                 status='ready' if plan.ready else 'blocked')
            store.add_event(conn, project['id'], 'plan', f'Story {plan.backlog_id} prepared',
                            {'plan_id': plan.id, 'uncertainties': list(plan.uncertainties)})
        return {'status': 'ready' if plan.ready else 'blocked', 'plan_id': plan.id}
    # Reuse the existing topic coverage; the new sections add consequential technical choices.
    missing = uncovered_topics(answers)
    if missing and not load_brief(repo):
        turn = next_turn(project, answers, db_path=db_path)
        questions = turn.questions or fallback_questions(missing[:2])
        phase = 'experience' if missing[0] in ('screens', 'errors', 'success') else 'product'
    elif not draft.get('technical_complete'):
        prompt = ('# Mode: technical\nDiscuss the existing system, stack, architecture, data, '
                  'integrations, security, hosting, limits and verification as relevant. '
                  'Reuse known choices. Do not force a database for a static product. '
                  'Ask up to two consequential questions with recommended options, topic technical. '
                  'Set done=true when technical choices are settled or explicitly delegated.\n'
                  + json.dumps({'context': project_context(project), 'answers': answers}))
        turn = _ask_agent(project, prompt, InterviewTurn, db_path=db_path, resume='Answers are saved.')
        if turn.done and any(a['topic'] == 'technical' for a in answers):
            draft['technical_complete'] = True
            with get_db(db_path) as conn:
                session = store.update_session(conn, session['id'], phase='execution', draft=draft)
                _enqueue(conn, session)
            return {'status': 'refining'}
        questions = turn.questions[:2] or [InterviewQuestion(topic='technical', question=
            'Are there technical or hosting constraints to add to the existing project spec? '
            'Say "you decide" to delegate remaining choices.')]
        questions = [q.model_copy(update={'topic': 'technical'}) for q in questions]
        phase = 'technical'
    elif not any(a['topic'] == 'execution' for a in answers):
        questions = [InterviewQuestion(topic='execution', question=
            'May routine planning, coding, checks and bounded repairs proceed automatically '
            'within the agreed requirements? Batch launches, material changes, budget increases '
            'and releases will still require your decision.', options=[
                {'label': 'Yes', 'description': 'Automate routine work within the agreed scope'},
                {'label': 'Pause', 'description': 'Keep preparation only; do not start builds yet'}])]
        phase = 'execution'
    else:
        spec = _ask_agent(project, build_stack_prompt(project, answers), ProjectSpec,
                          db_path=db_path, resume='The proposed stack will be saved for review.')
        draft['spec'] = spec.model_dump()
        with get_db(db_path) as conn:
            store.update_session(conn, session['id'], phase='review', draft=draft, status='needs_input')
            store.add_decision(conn, project['id'], f"{session['id']}:brief:{session['revision']}",
                               'Review product brief, technical choices and execution agreement',
                               session_id=session['id'], kind='brief', context={
                                   'brief': render_brief(project.get('name') or project['id'], answers),
                                   'spec': draft['spec'], 'effect': 'Type approve to publish this revision.'})
        return {'status': 'needs_input'}
    with get_db(db_path) as conn:
        _questions(conn, session, questions, phase)
    return {'status': 'needs_input'}


def start_backlog(project_ref: str, *, db_path: Path) -> dict:
    project = get_project(db_path, project_ref)
    if not load_brief(Path(project['repo_path'])):
        raise RunError('Approve the product interview before proposing stories')
    with get_db(db_path) as conn, store.atomic(conn):
        existing = [j for j in store.list_jobs(conn, project['id']) if j['kind'] == 'backlog'
                    and j['status'] in ('queued', 'running')]
        if existing:
            return existing[-1]
        return store.enqueue_job(conn, project['id'], 'backlog', f'backlog:{uuid.uuid4().hex}',
                                  {'context_revision': context_revision(project)})


def advance_backlog(job: dict, *, db_path: Path) -> dict:
    from factory.runs.backlog import _propose, build_backlog_prompt
    project = get_project(db_path, job['project_id'])
    if context_revision(project) != job['payload']['context_revision']:
        raise RunError('Brief changed; propose the backlog again')
    with get_db(db_path) as conn:
        current = list_backlog(conn, project['id'])
    stories = _propose(project, build_backlog_prompt(project, load_brief(Path(project['repo_path'])),
                                                    current, []), db_path=db_path)
    with get_db(db_path) as conn:
        store.add_decision(conn, project['id'], f"backlog:{job['id']}", 'Review the proposed backlog',
                           kind='backlog', context={'stories': [s.model_dump() for s in stories],
                                                   'context_revision': context_revision(project),
                                                   'effect': 'Type approve to save these stories. Existing refinement is retained.'})
    return {'status': 'needs_input'}


def answer_backlog(decision_id: str, text: str, *, db_path: Path) -> dict:
    from factory.domain.backlog import BacklogStory
    from factory.runs.backlog import _write_and_commit
    from factory.state.backlog import replace_unstarted
    if text.strip().lower() != 'approve':
        raise RunError('Type approve to accept this backlog; amend the brief before requesting a new proposal')
    with get_db(db_path) as conn:
        decision = store.get_decision(conn, decision_id)
    project = get_project(db_path, decision['project_id'])
    with project_lock(db_path, project['id']):
        if context_revision(project) != decision['context']['context_revision']:
            raise RunError('This backlog proposal refers to an older brief; propose it again')
        with get_db(db_path) as conn, store.atomic(conn):
            decision = store.get_decision(conn, decision_id)
            if decision['status'] == 'answered':
                return decision
            if store.legacy_workspace_busy(conn, project['id']):
                raise RunError('Wait for the legacy checkout run before publishing backlog evidence')
            stories = [BacklogStory.model_validate(s) for s in decision['context']['stories']]
            replace_unstarted(conn, project['id'], stories)
            result = store.answer_decision(conn, decision_id, text)
        _write_and_commit(project, db_path=db_path)
        return result
