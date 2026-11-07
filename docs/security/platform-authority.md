# Platform authority bootstrap and security model

KDO platform authority is distinct from workspace RBAC.

## Roles

- `platform.super_admin`: initial system owner; explicit human Supabase Auth principal UUID; platform-wide administration.
- `platform.operator`: explicitly delegated human principal; read-only platform health, workspace inventory, and operational visibility.
- `owner`: workspace owner only; never implies platform-wide access.
- machine/service identities: separate principal type; never receive human platform grants.

## Bootstrap

Set these server-side environment variables on the KDO backend only:

- `PLATFORM_SUPER_ADMIN_USER_IDS`: comma-separated Supabase Auth user UUIDs.
- `PLATFORM_OPERATOR_USER_IDS`: comma-separated Supabase Auth user UUIDs.

Only UUIDs are accepted. Entries are matched against the user ID returned by Supabase Auth `get_user(access_token)`. Email, `user_metadata`, request headers, workspace role, and browser-provided scopes are not consulted.

Leave both values empty until the owner account UUID has been confirmed. Never put these values in frontend environment variables or commit them to the repository.

The canonical `/api/v1/auth/exchange` response carries the server-resolved platform grant to KCPG Gateway. KCPG signs it into its own short-lived gateway session. The browser cannot self-assign the claim.

## Permission boundary

- Super Admin may enter platform scope and cross workspace boundaries.
- Platform Operator may view platform health and workspace inventory; it does not receive cross-workspace mutation authority.
- Workspace owners remain constrained to their own workspace.
- Platform grants do not bypass Council, policy evaluation, Action Kernel approval, audit, or rollback controls for sensitive operations.
- Machine credentials remain machine identities and cannot satisfy human platform-role checks.

## Operational rollout

1. Merge the backend and KCPG security PRs only after CI and review.
2. Configure the initial owner UUID in the backend environment, not in the browser.
3. Configure `IDENTITY_EXCHANGE_ENABLED=true` and a dedicated strong `PYTHON_SERVICE_TOKEN` on KCPG.
4. Deploy to staging only, then verify owner, workspace owner, operator, and machine-identity cases.
5. Promote only after explicit approval and evidence. Production remains untouched by this change set.
