"""Dependency-aware concurrent scheduler and scoped agent messaging primitives.

This scheduler is an opt-in local orchestration primitive. It is not a durable
worker queue: production must back it with authoritative leases and recovery.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from typing import Callable, Mapping, Any

from services.kdo_engineering_team_events import TeamEventStore


class SchedulerError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    depends_on: tuple[str, ...]
    run: Callable[[Mapping[str, Any]], Any]


def run_dependency_graph(tasks: tuple[ScheduledTask, ...] | list[ScheduledTask], *, max_workers: int = 4) -> dict[str, dict]:
    """Run independent tasks concurrently and descendants only after success."""
    if not isinstance(max_workers, int) or max_workers < 1 or max_workers > 32:
        raise SchedulerError("max_workers_out_of_range")
    graph = {}
    for task in tasks:
        if not isinstance(task, ScheduledTask) or not task.task_id.strip() or not callable(task.run):
            raise SchedulerError("invalid_scheduled_task")
        if task.task_id in graph:
            raise SchedulerError("duplicate_task_id:" + task.task_id)
        graph[task.task_id] = task
    for task in graph.values():
        unknown = set(task.depends_on) - graph.keys()
        if unknown:
            raise SchedulerError("unknown_dependencies:" + ",".join(sorted(unknown)))
        if task.task_id in task.depends_on:
            raise SchedulerError("self_dependency:" + task.task_id)
    # Detect cycles before starting any user-provided work.
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise SchedulerError("dependency_cycle_detected")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node].depends_on:
            visit(dep)
        visiting.remove(node); visited.add(node)
    for node in graph:
        visit(node)

    results: dict[str, dict] = {}
    pending = set(graph)
    running = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kdo-agent") as pool:
        while pending or running:
            progress = False
            for node in sorted(tuple(pending)):
                task = graph[node]
                if not all(dep in results for dep in task.depends_on):
                    continue
                failed_deps = [dep for dep in task.depends_on if results[dep]["status"] != "succeeded"]
                if failed_deps:
                    results[node] = {"status": "blocked", "reason": "dependency_failed", "blocked_by": failed_deps}
                    pending.remove(node); progress = True
                    continue
                inputs = {dep: results[dep].get("output") for dep in task.depends_on}
                running[pool.submit(task.run, inputs)] = node
                pending.remove(node); progress = True
                if len(running) >= max_workers:
                    break
            if running:
                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    node = running.pop(future)
                    try:
                        results[node] = {"status": "succeeded", "output": future.result()}
                    except Exception as exc:
                        results[node] = {"status": "failed", "reason": type(exc).__name__}
                continue
            if pending and not progress:
                raise SchedulerError("scheduler_stalled")
    return results


def send_agent_message(*, event_store: TeamEventStore, task_id: str, tenant_id: str,
                       workspace_id: str, sender_agent_id: str, recipient_agent_id: str,
                       message_type: str, body: Mapping[str, Any]) -> dict:
    """Persist a scoped inter-agent message as an event; not a permission grant."""
    values = (task_id, tenant_id, workspace_id, sender_agent_id, recipient_agent_id, message_type)
    if not all(isinstance(x, str) and x.strip() for x in values):
        raise SchedulerError("message_scope_sender_recipient_and_type_required")
    if sender_agent_id == recipient_agent_id:
        raise SchedulerError("self_message_not_allowed")
    if not isinstance(body, Mapping):
        raise SchedulerError("message_body_must_be_mapping")
    return event_store.append(task_id=task_id, tenant_id=tenant_id, workspace_id=workspace_id,
        event_type="agent_message", actor_id=sender_agent_id,
        payload={"recipient_agent_id": recipient_agent_id, "message_type": message_type,
                 "body": dict(body), "authority": "coordination_only"})
