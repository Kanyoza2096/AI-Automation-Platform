# GitHub and Render Enterprise Adapters

These are provider adapters behind the KDO Connector Fabric, separate from the
legacy social-account connector factory. They do not create a second execution
authority and do not bypass the Action Kernel, authorization, execution ledger,
canonical receipt protocol, or reconciliation workflows.

## Available capabilities

### GitHub
- Read repository metadata, issues, pull requests, and workflow runs.
- Create issues and issue comments.
- Dispatch workflows, rerun/cancel workflow runs, and merge pull requests under
  explicit high-risk gates.
- Reconcile workflow dispatches by searching for candidate runs. No matching
  run is reported as inconclusive, never as proof that the dispatch failed.

### Render
- List services and read service/deployment details and deployment history.
- Trigger a service deployment with an optional commit ID/cache policy.
- Request deployment cancellation.
- Reconcile a deployment by reading provider state and checking service identity.

## Safety contract

- Read-only operations are enabled by default.
- Mutations require `allow_mutations=True` on the adapter instance.
- High-risk operations (workflow dispatch/rerun/cancel, PR merge, Render deploy
  and cancel) also require `allow_high_risk=True` and explicit `confirm=True`.
- A production Render deploy should additionally be called with `production=True`
  only after the upstream governance/approval path authorizes it.
- Mutating requests are never automatically retried. Transport failures and
  provider 5xx responses are treated as ambiguous and require reconciliation.
- HTTP acceptance is not the same as terminal success. GitHub workflow dispatch
  and Render deploy/cancel return uncertain/in-progress evidence until provider
  state is queried. A missing provider object ID never becomes fabricated success.
- Credentials are passed at runtime (`GITHUB_TOKEN`, `RENDER_API_KEY`, or explicit
  constructor arguments). They are never written to manifests or logs by these
  adapters. Use least-privilege tokens and secret storage in deployment.

## Example

```python
from services.connectors.enterprise_registry import create_enterprise_adapter

github = create_enterprise_adapter("github", allow_mutations=False)  # reads only
repository = github.get_repository("your-org", "your-repo")

render = create_enterprise_adapter("render", allow_mutations=False)  # reads only
services = render.list_services(limit=20)
```

To execute a mutation, instantiate an adapter with explicit mutation policy and
invoke it only from an Action Kernel handler after authorization. These adapters
are implemented and unit-tested; they are **not automatically wired into live
production credentials or auto-registered as executable Action Kernel contracts**.
Live staging certification remains part of Phase 6.

## Action Kernel registration

During composition, create adapters from trusted deployment configuration and
register their mutating action contracts with `register_github_actions(kernel,
adapter)` and `register_render_actions(kernel, adapter)` from
`services.connectors.enterprise_action_contracts`. Those contracts require
approval and mark high-impact operations as irreversible/high-risk. Do not expose
adapter objects or provider tokens to user-controlled action parameters. Keep
read-only provider methods behind application query/use-case handlers and normal
workspace authorization.
