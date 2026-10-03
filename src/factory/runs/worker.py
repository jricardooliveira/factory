"""Detached durable worker. Human waits consume records, not executor threads."""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from factory.runs.events import RunInterrupted
from factory.state import workflow as store
from factory.state.db import get_db, init_db


def worker_status(*, db_path: Path) -> str:
    lock = db_path.parent / 'worker.lock'
    if not lock.exists():
        return 'offline'
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'active'
        fcntl.flock(handle, fcntl.LOCK_UN)
    return 'offline'


def start_worker(*, db_path: Path, max_workers: int = 4) -> int | None:
    if worker_status(db_path=db_path) == 'active':
        return None
    db_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    source = str(Path(__file__).resolve().parents[2])
    env['PYTHONPATH'] = source + os.pathsep + env.get('PYTHONPATH', '')
    with (db_path.parent / 'worker.log').open('a') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'factory.runs.worker', '--db', str(db_path.resolve()),
             '--workers', str(max_workers)], stdin=subprocess.DEVNULL, stdout=log,
            stderr=log, env=env, start_new_session=True,
        )
    return process.pid


def _execute(job: dict, db_path: Path) -> None:
    def event(_event) -> None:
        with get_db(db_path) as conn:
            current = store.get_job(conn, job['id'])
        if current['status'] != 'running' or current['payload'].get('stop_requested'):
            raise KeyboardInterrupt('Stopped at a pipeline boundary')
    try:
        event(None)
        if job['kind'] == 'backlog':
            from factory.runs.refinement import advance_backlog
            result = advance_backlog(job, db_path=db_path)
        elif job['kind'] == 'refine':
            from factory.runs.refinement import advance_refinement
            result = advance_refinement(job, db_path=db_path)
        else:
            from factory.runs.batches import dispatch_job
            result = dispatch_job(job, db_path=db_path, on_event=event)
        with get_db(db_path) as conn:
            store.finish_job(conn, job['id'], job['owner'], result=result,
                             run_id=result.get('run_id'))
            store.add_event(conn, job['project_id'], 'job',
                            f"{job['kind']}: {result.get('status', 'completed')}", result)
    except BaseException as exc:
        # KeyboardInterrupt here is a per-job safe stop, never process cancellation.
        if isinstance(exc, (SystemExit, GeneratorExit)):
            raise
        status = 'interrupted' if isinstance(exc, (KeyboardInterrupt, RunInterrupted)) else 'failed'
        with get_db(db_path) as conn:
            try:
                store.finish_job(conn, job['id'], job['owner'], status=status, error=str(exc))
            except ValueError:
                pass  # lost lease already marked interrupted; never reclaim it blindly
            store.add_event(conn, job['project_id'], status, f"{job['kind']}: {exc}",
                            {'job_id': job['id']})


def serve(*, db_path: Path, max_workers: int = 4, stop: threading.Event | None = None) -> None:
    if max_workers < 2:
        raise ValueError('Use at least two workers so refinement can progress independently')
    init_db(db_path)
    stop = stop or threading.Event()
    with (db_path.parent / 'worker.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        owner = f'{os.getpid()}:{uuid.uuid4().hex}'
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            active = {}
            while not stop.is_set():
                for identity, future in list(active.items()):
                    if future.done():
                        future.result()
                        del active[identity]
                    else:
                        with get_db(db_path) as conn:
                            store.heartbeat_job(conn, identity, owner)
                while len(active) < max_workers:
                    with get_db(db_path) as conn:
                        job = store.claim_job(conn, owner)
                    if job is None:
                        break
                    active[job['id']] = pool.submit(_execute, job, db_path)
                stop.wait(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    serve(db_path=args.db, max_workers=args.workers)
