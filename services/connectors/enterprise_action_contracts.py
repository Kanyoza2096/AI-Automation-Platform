"""Governed Action Kernel contracts for GitHub and Render provider operations.

Call these registration helpers during application composition. Provider adapters
must be configured explicitly; contracts never read credentials from action
parameters and never permit a caller-provided `confirm` flag to bypass governance.
"""
from __future__ import annotations
from typing import Any
from services.action_kernel import ActionContract


def register_github_actions(kernel: Any, adapter: Any) -> tuple[str, ...]:
    """Register GitHub mutations; Action Kernel approval is the authorization gate."""
    actions = (
        ActionContract("github.create_issue", risk_level="medium",
            required_parameters=("owner", "repo", "title"), requires_approval=True,
            handler=lambda p, _i: adapter.create_issue(p["owner"], p["repo"], title=p["title"], body=p.get("body", ""), labels=p.get("labels"), assignees=p.get("assignees"))),
        ActionContract("github.add_issue_comment", risk_level="medium",
            required_parameters=("owner", "repo", "issue_number", "body"), requires_approval=True,
            handler=lambda p, _i: adapter.add_issue_comment(p["owner"], p["repo"], int(p["issue_number"]), body=p["body"])),
        ActionContract("github.dispatch_workflow", risk_level="critical",
            required_parameters=("owner", "repo", "workflow_id", "ref"), requires_approval=True, irreversible=True,
            handler=lambda p, _i: adapter.dispatch_workflow(p["owner"], p["repo"], workflow_id=p["workflow_id"], ref=p["ref"], inputs=p.get("inputs"), confirm=True)),
        ActionContract("github.rerun_workflow", risk_level="high",
            required_parameters=("owner", "repo", "run_id"), requires_approval=True, irreversible=True,
            handler=lambda p, _i: adapter.rerun_workflow(p["owner"], p["repo"], int(p["run_id"]), confirm=True)),
        ActionContract("github.cancel_workflow_run", risk_level="high",
            required_parameters=("owner", "repo", "run_id"), requires_approval=True, irreversible=True,
            handler=lambda p, _i: adapter.cancel_workflow_run(p["owner"], p["repo"], int(p["run_id"]), confirm=True)),
        ActionContract("github.merge_pull_request", risk_level="critical",
            required_parameters=("owner", "repo", "pull_number"), requires_approval=True, irreversible=True,
            handler=lambda p, _i: adapter.merge_pull_request(p["owner"], p["repo"], int(p["pull_number"]), merge_method=p.get("merge_method", "squash"), commit_title=p.get("commit_title"), confirm=True)),
    )
    for action in actions: kernel.register(action)
    return tuple(a.action_type for a in actions)


def register_render_actions(kernel: Any, adapter: Any) -> tuple[str, ...]:
    """Register Render deployment mutations; production approval remains upstream."""
    actions = (
        ActionContract("render.trigger_deploy", risk_level="critical",
            required_parameters=("service_id",), requires_approval=True, irreversible=True,
            handler=lambda p, _i: adapter.trigger_deploy(p["service_id"], commit_id=p.get("commit_id"), clear_cache=bool(p.get("clear_cache", False)), confirm=True, production=bool(p.get("production", False)), confirm_production=bool(p.get("production", False)))),
        ActionContract("render.cancel_deploy", risk_level="high",
            required_parameters=("deploy_id",), requires_approval=True, irreversible=True,
            handler=lambda p, _i: adapter.cancel_deploy(p["deploy_id"], confirm=True)),
    )
    for action in actions: kernel.register(action)
    return tuple(a.action_type for a in actions)


def register_modern_actions(kernel: Any, adapters: dict[str, Any]) -> tuple[str, ...]:
    """Register the curated high-value provider actions behind Action Kernel governance."""
    actions = []
    if "stripe" in adapters:
        a=adapters["stripe"]
        actions += [
            ActionContract("stripe.create_payment_intent", risk_level="high", required_parameters=("amount","currency","idempotency_key"), requires_approval=True, irreversible=True,
                           handler=lambda p,_i:a.create_payment_intent(amount=int(p["amount"]),currency=p["currency"],idempotency_key=p["idempotency_key"],customer=p.get("customer"))),
        ]
    if "shopify" in adapters:
        a=adapters["shopify"]
        actions += [ActionContract("shopify.cancel_order", risk_level="high", required_parameters=("order_id",), requires_approval=True, irreversible=True,
                           handler=lambda p,_i:a.cancel_order(p["order_id"],reason=p.get("reason"),confirm=True))]
    if "slack" in adapters:
        a=adapters["slack"]
        actions += [ActionContract("slack.post_message", risk_level="medium", required_parameters=("channel","text"), requires_approval=True,
                           handler=lambda p,_i:a.post_message(channel=p["channel"],text=p["text"]))]
    if "jira" in adapters:
        a=adapters["jira"]
        actions += [ActionContract("jira.create_issue", risk_level="medium", required_parameters=("project_key","issue_type","summary"), requires_approval=True,
                           handler=lambda p,_i:a.create_issue(project_key=p["project_key"],issue_type=p["issue_type"],summary=p["summary"],description=p.get("description", "")))]
    if "pagerduty" in adapters:
        a=adapters["pagerduty"]
        actions += [ActionContract("pagerduty.create_incident", risk_level="critical", required_parameters=("service_id","title"), requires_approval=True, irreversible=True,
                           handler=lambda p,_i:a.create_incident(service_id=p["service_id"],title=p["title"],urgency=p.get("urgency","high")))]
    if "datadog" in adapters:
        a=adapters["datadog"]
        actions += [ActionContract("datadog.create_downtime", risk_level="high", required_parameters=("scope","message"), requires_approval=True, irreversible=True,
                           handler=lambda p,_i:a.create_downtime(scope=p["scope"],message=p["message"]))]
    if "cloudflare" in adapters:
        a=adapters["cloudflare"]
        actions += [ActionContract("cloudflare.purge_cache", risk_level="critical", required_parameters=("zone_id",), requires_approval=True, irreversible=True,
                           handler=lambda p,_i:a.purge_cache(p["zone_id"],confirm=True))]
    if "twilio" in adapters:
        a=adapters["twilio"]
        actions += [ActionContract("twilio.send_sms", risk_level="high", required_parameters=("from_","to","body"), requires_approval=True,
                           handler=lambda p,_i:a.send_sms(from_=p["from_"],to=p["to"],body=p["body"]))]
    if "google_workspace" in adapters:
        actions.extend(register_google_workspace_actions(kernel, adapters["google_workspace"]))
    if "aws" in adapters:
        a=adapters["aws"]
        actions += [
            ActionContract("aws.update_ecs_service", risk_level="critical", required_parameters=("cluster","service","desired_count"), requires_approval=True, irreversible=True,
                           handler=lambda p,_i:a.update_ecs_service(cluster=p["cluster"],service=p["service"],desired_count=int(p["desired_count"]),confirm=True)),
            ActionContract("aws.put_object", risk_level="high", required_parameters=("bucket","key","body"), requires_approval=True,
                           handler=lambda p,_i:a.put_object(bucket=p["bucket"],key=p["key"],body=p["body"])),
        ]
    for action in actions: kernel.register(action)
    return tuple(a.action_type for a in actions)


def register_google_workspace_actions(kernel: Any, adapter: Any) -> tuple[str, ...]:
    """Register curated Google Workspace mutations behind Action Kernel governance."""
    actions = (
        ActionContract("google_workspace.send_gmail_message", risk_level="high", required_parameters=("raw_message",), requires_approval=True,
            handler=lambda p, _i: adapter.send_gmail_message(raw_message=p["raw_message"], user_id=p.get("user_id", "me"), confirm=True)),
        ActionContract("google_workspace.create_calendar_event", risk_level="medium", required_parameters=("calendar_id", "event"), requires_approval=True,
            handler=lambda p, _i: adapter.create_calendar_event(calendar_id=p["calendar_id"], event=p["event"], confirm=True)),
        ActionContract("google_workspace.append_sheet_values", risk_level="medium", required_parameters=("spreadsheet_id", "range_name", "values"), requires_approval=True,
            handler=lambda p, _i: adapter.append_sheet_values(spreadsheet_id=p["spreadsheet_id"], range_name=p["range_name"], values=p["values"], confirm=True)),
        ActionContract("google_workspace.create_doc", risk_level="medium", required_parameters=("title",), requires_approval=True,
            handler=lambda p, _i: adapter.create_doc(title=p["title"], confirm=True)),
    )
    for action in actions: kernel.register(action)
    return tuple(a.action_type for a in actions)
