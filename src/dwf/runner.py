"""Executing a task's steps as a dependency graph.

Every task runs through the same DAG executor. In "serial" mode each step depends on the previous one; in
"parallel" mode steps are independent. A step whose dependency did not succeed is skipped.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from graphlib import TopologicalSorter

from dwf.errors import StepFailed
from dwf.launch import Context, run_step
from dwf.task import Step, Task

log = logging.getLogger('dwf')

MAX_WORKERS = 8


@dataclass(frozen=True)
class StepResult:
    status: str          # 'ok' | 'failed' | 'skipped'
    message: str
    seconds: float = 0.0


def dependencies(task: Task) -> dict[str, tuple[str, ...]]:
    deps = {}
    for i, step in enumerate(task.steps):
        deps[step.id] = (task.steps[i - 1].id,) if task.mode == 'serial' and i > 0 else ()
    return deps


def _timed(step: Step, ctx: Context) -> StepResult:
    log.info('%s: starting', step.id)
    t0 = time.monotonic()
    try:
        message = run_step(step, ctx)
        status = 'ok'
    except StepFailed as e:
        message, status = str(e), 'failed'
    except Exception as e:  # a bug, not a step failure; keep the other steps running
        log.exception('%s: internal error', step.id)
        message, status = f'internal error: {e}', 'failed'
    result = StepResult(status, message, time.monotonic() - t0)
    log.info('%s: %s: %s', step.id, status, message)
    return result


def execute(task: Task, ctx: Context) -> dict[str, StepResult]:
    """Run all steps; return results keyed by step ID, in task order."""
    steps = {s.id: s for s in task.steps}
    deps = dependencies(task)
    graph = TopologicalSorter(deps)
    graph.prepare()
    results: dict[str, StepResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, len(steps))), thread_name_prefix='dwf') as pool:
        running: dict[Future, str] = {}
        while graph.is_active():
            for sid in graph.get_ready():
                blocked = [d for d in deps[sid] if results[d].status != 'ok']
                if blocked:
                    results[sid] = StepResult('skipped', f'{", ".join(blocked)} did not succeed')
                    log.info('%s: skipped: %s', sid, results[sid].message)
                    graph.done(sid)
                else:
                    running[pool.submit(_timed, steps[sid], ctx)] = sid
            if running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    sid = running.pop(future)
                    results[sid] = future.result()
                    graph.done(sid)
    return {sid: results[sid] for sid in steps}
