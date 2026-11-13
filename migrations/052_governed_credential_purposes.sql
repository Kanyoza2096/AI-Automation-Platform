-- Additive credential-purpose metadata. Existing tokens remain workspace_api by default.
-- Human platform authority is deliberately not representable in this machine-token schema.
ALTER TABLE public.api_tokens
  ADD COLUMN IF NOT EXISTS credential_purpose TEXT NOT NULL DEFAULT 'workspace_api',
  ADD COLUMN IF NOT EXISTS resource_constraints JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.api_tokens
  DROP CONSTRAINT IF EXISTS api_tokens_credential_purpose_check;
ALTER TABLE public.api_tokens
  ADD CONSTRAINT api_tokens_credential_purpose_check
  CHECK (credential_purpose IN ('workspace_api', 'service_integration', 'website_connector'));

CREATE INDEX IF NOT EXISTS idx_api_tokens_workspace_purpose
  ON public.api_tokens (workspace_id, credential_purpose);
