-- Customer-managed inbound event sources for Business Automation.
-- Secrets are stored only as SHA-256 digests; the plaintext secret is returned once at creation.
CREATE TABLE IF NOT EXISTS business_automation_event_sources (
  source_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  name TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'generic_webhook',
  event_types JSONB NOT NULL DEFAULT '[]'::jsonb,
  secret_hash TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_event_at TIMESTAMPTZ,
  events_received BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ba_event_sources_workspace ON business_automation_event_sources(workspace_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ba_event_sources_workspace_name ON business_automation_event_sources(workspace_id, lower(name));
ALTER TABLE business_automation_event_sources ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ba_event_sources_select ON business_automation_event_sources;
CREATE POLICY ba_event_sources_select ON business_automation_event_sources FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS ba_event_sources_insert ON business_automation_event_sources;
CREATE POLICY ba_event_sources_insert ON business_automation_event_sources FOR INSERT WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));
DROP POLICY IF EXISTS ba_event_sources_update ON business_automation_event_sources;
CREATE POLICY ba_event_sources_update ON business_automation_event_sources FOR UPDATE USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text)) WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

CREATE TABLE IF NOT EXISTS business_automation_event_receipts (
  receipt_id BIGSERIAL PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES business_automation_event_sources(source_id) ON DELETE CASCADE,
  workspace_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_name TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ,
  publish_error TEXT,
  UNIQUE(source_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_ba_event_receipts_workspace ON business_automation_event_receipts(workspace_id, received_at DESC);
ALTER TABLE business_automation_event_receipts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ba_event_receipts_select ON business_automation_event_receipts;
CREATE POLICY ba_event_receipts_select ON business_automation_event_receipts FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

-- Atomic idempotent receipt insert helper. The API uses the unique constraint as a replay fence.

CREATE OR REPLACE FUNCTION public.claim_business_automation_event_receipt(
  p_source_id TEXT, p_workspace_id TEXT, p_event_id TEXT, p_event_name TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r business_automation_event_receipts;
BEGIN
  SELECT * INTO r FROM business_automation_event_receipts
   WHERE source_id=p_source_id AND event_id=p_event_id FOR UPDATE;
  IF FOUND THEN
    IF r.published_at IS NOT NULL THEN RETURN FALSE; END IF;
    UPDATE business_automation_event_receipts SET received_at=now(), event_name=p_event_name, publish_error=NULL WHERE receipt_id=r.receipt_id;
    RETURN TRUE;
  END IF;
  INSERT INTO business_automation_event_receipts(source_id,workspace_id,event_id,event_name)
  VALUES(p_source_id,p_workspace_id,p_event_id,p_event_name);
  RETURN TRUE;
END; $$;
REVOKE ALL ON FUNCTION public.claim_business_automation_event_receipt(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_business_automation_event_receipt(TEXT,TEXT,TEXT,TEXT) TO service_role;
