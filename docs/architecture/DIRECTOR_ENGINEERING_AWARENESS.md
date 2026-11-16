# Director Engineering Awareness

## Purpose

Director Engineering Awareness is a read-only, on-demand evidence snapshot of recent engineering changes across KDO's configured repositories. It correlates GitHub merge records with Render deployment records when the exact commit can be established.

It does not create tasks, plans, missions, approvals, commits, pull requests, or deployments. It does not grant execution authority.

## Access

The endpoint is **GET /director/engineering-awareness**, exposed through the authenticated KCPG Director proxy. It requires a valid authenticated session and workspace-owner role, or trusted platform/species authority. The KCPG proxy independently enforces an authenticated workspace session and read scope.

## Sources and configuration

### GitHub

- Uses the existing GitHub connector and its configured token (GITHUB_TOKEN or the connector's existing token configuration).
- Default repositories:
  - Kanyoza2096/AI-Automation-Platform
  - Kanyoza2096/KDO-Control-plane-
  - Kanyoza2096/KCPG-Gateway
- Override the repository list with comma-separated KDO_ENGINEERING_REPOSITORIES.
- The snapshot covers merged pull requests from the preceding 30 days, bounded to 15 merged pull requests per repository.
- The latest Actions workflow is displayed separately. It is not attributed to a merge unless a future implementation adds explicit commit correlation.

### Render

- Uses RENDER_API_KEY and the existing Render enterprise adapter with mutations disabled.
- Services are automatically matched when Render service metadata contains a repository URL matching one of the configured GitHub repositories.
- If service metadata does not expose a repository URL, configure an explicit mapping:
  KDO_ENGINEERING_RENDER_SERVICE_MAP=service-id=owner/repo,another-service-id=owner/another-repo
- Optionally restrict the inspected services with comma-separated KDO_ENGINEERING_RENDER_SERVICE_IDS.
- The snapshot inspects at most 30 services and 10 recent deployments per service.
- Configured service IDs without a repository mapping can be displayed, but are never used to attribute deployments to a repository.

## Evidence semantics

| State | Evidence required |
|---|---|
| Merged | GitHub pull request has an authoritative merged_at timestamp |
| Deployed | Render reports a live deployment whose commit SHA exactly matches the PR merge SHA or head SHA |
| In progress | Exact commit match exists and Render reports an in-progress state |
| Deployment failed | Exact commit match exists and Render reports a failed/cancelled state |
| Operationally verified | Not yet implemented; requires a separate, capability-specific runtime acceptance contract |

A merge does not prove deployment. A Render live state does not prove application behavior, user-visible correctness, or end-to-end operational acceptance. No ancestry inference or approximate SHA matching is performed.

## Current limitations

- The endpoint builds a fresh snapshot on request; it is not yet a durable chronological engineering ledger.
- Operational verification is intentionally unverified until a separate runtime acceptance contract is implemented.
- GitHub and Render source availability is reported independently. Missing credentials, failed provider calls, or unmatched services remain visible as unavailable/unmatched rather than being interpreted as zero engineering activity.
- Provider calls are bounded and read-only.

## Security boundary

The Render adapter is instantiated with allow_mutations=False. The GitHub connector calls used by this service are GET-only. The endpoint does not call the Action Kernel and cannot trigger repository or deployment mutations.
