-- V73 website connector: no schema change is required because website
-- connections use the existing workspace-scoped social_accounts contract.
-- This migration documents the supported platform and adds an index useful
-- for exact workspace/website-account resolution.
CREATE INDEX IF NOT EXISTS idx_social_accounts_workspace_platform
    ON public.social_accounts(workspace_id, platform);

COMMENT ON COLUMN public.social_accounts.platform_config IS
'Platform-specific connector configuration. For platform=website, configure a REST CMS adapter with base_url, publish_path, update_path, lookup_path, auth_mode, and content_type. Secrets belong in encrypted access_token or api_key storage; never put bearer tokens in URLs.';
