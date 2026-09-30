-- KDO Next.js Website Integration Registry
-- Registration metadata is durable; website credentials remain in the encrypted
-- social_accounts credential path consumed by the canonical WebsiteConnector.
CREATE TABLE IF NOT EXISTS public.website_integrations (
    integration_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    app_name TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT 'production',
    base_url TEXT NOT NULL,
    publish_path TEXT NOT NULL,
    lookup_path_template TEXT NOT NULL DEFAULT '',
    edit_path_template TEXT NOT NULL DEFAULT '',
    delete_path_template TEXT NOT NULL DEFAULT '',
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'registered',
    secret_hash TEXT,
    last_event_at TIMESTAMPTZ,
    last_health_at TIMESTAMPTZ,
    last_health_ok BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, integration_id),
    CONSTRAINT website_integrations_https CHECK (base_url LIKE 'https://%'),
    CONSTRAINT website_integrations_status CHECK (status IN ('registered','configured','connected','degraded','disconnected','revoked'))
);

CREATE INDEX IF NOT EXISTS idx_website_integrations_workspace
    ON public.website_integrations(workspace_id);

CREATE TABLE IF NOT EXISTS public.website_integration_events (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    integration_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version TEXT NOT NULL DEFAULT '1.0',
    occurred_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(workspace_id, integration_id, event_id),
    CONSTRAINT website_event_integration_fk
        FOREIGN KEY (workspace_id, integration_id)
        REFERENCES public.website_integrations(workspace_id, integration_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_website_integration_events_workspace_time
    ON public.website_integration_events(workspace_id, occurred_at DESC);

ALTER TABLE public.website_integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.website_integration_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS website_integrations_workspace_isolation ON public.website_integrations;
CREATE POLICY website_integrations_workspace_isolation ON public.website_integrations
    USING (workspace_id = current_setting('request.jwt.claims', true)::json->>'workspace_id')
    WITH CHECK (workspace_id = current_setting('request.jwt.claims', true)::json->>'workspace_id');

DROP POLICY IF EXISTS website_integration_events_workspace_isolation ON public.website_integration_events;
CREATE POLICY website_integration_events_workspace_isolation ON public.website_integration_events
    USING (workspace_id = current_setting('request.jwt.claims', true)::json->>'workspace_id')
    WITH CHECK (workspace_id = current_setting('request.jwt.claims', true)::json->>'workspace_id');
