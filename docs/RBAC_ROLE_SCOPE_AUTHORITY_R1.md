# RBAC Role/Scope Authority R1

`workspace_members.user_id` + `workspace_members.workspace_id` is the authoritative
human role binding. The `owner` role is the highest human workspace role.

The auth exchange accepts an optional target workspace and re-resolves membership
for that workspace. This prevents role/scope carry-over when a user switches
workspaces.

KCPG consumes this identity result and projects its gateway scopes from the role.
Human role/scope authority is not configured through gateway environment variables.
