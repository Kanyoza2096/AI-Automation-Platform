# Platform authority bootstrap and security model

Platform authority is separate from workspace RBAC.

- `platform.super_admin`: full platform-wide human system-owner authority.
- `platform.operator`: delegated read-only platform health, workspace inventory, and operational visibility.
- `owner`: workspace owner only; never implies platform-wide access.
- Machine/service identities remain separate and never receive human platform grants.

Configure server-side backend environment variables only:
- `PLATFORM_SUPER_ADMIN_USER_IDS`: comma-separated Supabase Auth user UUIDs.
- `PLATFORM_OPERATOR_USER_IDS`: comma-separated Supabase Auth user UUIDs.

Only UUIDs are accepted and matched against the user ID returned by Supabase Auth `get_user(access_token)`. Email, `user_metadata`, workspace role, browser claims, and request headers are not authority sources. Leave both values empty until the initial owner's UUID is confirmed. Never expose these variables to the frontend or commit their values.

The canonical `/api/v1/auth/exchange` endpoint sends the server-resolved grant to KCPG Gateway, which signs it into a short-lived session. The browser cannot self-assign the claim.

Platform Super Admin may select workspaces and hold full authenticated platform authority. Platform Operator remains read-only and tenant-scoped for mutations. Sensitive actions still pass through existing policy, Council/Action Kernel governance, Guardian leases, audit, and rollback controls.

Rollout order: merge into staging first, set the owner UUID only in staging, validate two-workspace access and machine/workspace-owner denial, then seek explicit production approval.