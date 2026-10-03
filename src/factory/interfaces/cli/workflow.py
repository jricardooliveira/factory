"""Durable workflow controls shared with the board."""
from __future__ import annotations

import argparse
import json

from factory.interfaces.cli.common import db_path, fail
from factory.runs.batches import launch_batch, propose_batch, queue_integration, queue_release
from factory.runs.events import RunError
from factory.runs.dashboard import dashboard, pause_project, stop_job
from factory.runs.refinement import answer, start_refinement
from factory.runs.worker import serve, start_worker


def work_command(args: list[str]) -> None:
    parser = argparse.ArgumentParser(prog='factory work')
    sub = parser.add_subparsers(dest='action', required=True)
    status = sub.add_parser('status'); status.add_argument('project', nargs='?')
    refine = sub.add_parser('refine'); refine.add_argument('project'); refine.add_argument('--story', type=int)
    respond = sub.add_parser('answer'); respond.add_argument('decision'); respond.add_argument('answer')
    propose = sub.add_parser('propose'); propose.add_argument('project'); propose.add_argument('--limit', type=int, default=2)
    propose.add_argument('--budget', type=float)
    launch = sub.add_parser('launch'); launch.add_argument('proposal'); launch.add_argument('--stories')
    integrate = sub.add_parser('integrate'); integrate.add_argument('proposal')
    release = sub.add_parser('release'); release.add_argument('decision')
    for name in ('pause', 'resume'):
        control = sub.add_parser(name); control.add_argument('project')
    stop = sub.add_parser('stop'); stop.add_argument('job')
    worker = sub.add_parser('worker'); worker.add_argument('--foreground', action='store_true')
    worker.add_argument('--workers', type=int, default=4)
    opts = parser.parse_args(args)
    db = db_path()
    try:
        queued = False
        if opts.action == 'status':
            result = dashboard(opts.project, db_path=db)
        elif opts.action == 'refine':
            result = start_refinement(opts.project, opts.story, db_path=db); queued = True
        elif opts.action == 'answer':
            result = answer(opts.decision, opts.answer, db_path=db); queued = True
        elif opts.action == 'propose':
            result = propose_batch(opts.project, db_path=db, limit=opts.limit, budget_usd=opts.budget)
        elif opts.action == 'launch':
            selected = [int(i) for i in opts.stories.split(',')] if opts.stories else None
            result = launch_batch(opts.proposal, db_path=db, selected_ids=selected); queued = True
        elif opts.action == 'integrate':
            result = queue_integration(opts.proposal, db_path=db); queued = True
        elif opts.action == 'release':
            result = queue_release(opts.decision, db_path=db); queued = True
        elif opts.action in ('pause', 'resume'):
            pause_project(opts.project, opts.action == 'pause', db_path=db)
            result = {'paused': opts.action == 'pause'}
        elif opts.action == 'stop':
            stop_job(opts.job, db_path=db); result = {'stop_requested': opts.job}
        elif opts.foreground:
            serve(db_path=db, max_workers=opts.workers); return
        else:
            result = {'worker_pid': start_worker(db_path=db, max_workers=opts.workers)}
        if queued:
            start_worker(db_path=db)
        print(json.dumps(result, indent=2, default=str))
    except (ValueError, OSError, RuntimeError, RunError) as exc:
        fail(str(exc))
