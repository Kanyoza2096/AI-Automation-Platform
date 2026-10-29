-- Phase 4: durable IT deployment provider identity and explicit version mapping.
-- These mappings are evidence/configuration, not execution authority.
CREATE TABLE IF NOT EXISTS public.it_service_provider_bindings (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, service_id TEXT NOT NULL,
    provider TEXT NOT NULL, provider_service_id TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'production', status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(workspace_id, service_id, provider, environment)
);
CREATE INDEX IF NOT EXISTS idx_it_service_provider_bindings_workspace_service
    ON public.it_service_provider_bindings(workspace_id, service_id, provider);
CREATE TABLE IF NOT EXISTS public.it_deployment_version_references (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, service_id TEXT NOT NULL,
    version TEXT NOT NULL, provider TEXT NOT NULL, provider_version_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(workspace_id, service_id, version, provider)
);
CREATE INDEX IF NOT EXISTS idx_it_deployment_version_refs_workspace_service
    ON public.it_deployment_version_references(workspace_id, service_id, provider, version);
