-- Business Activation v1: extraction audit + confirmed, versioned facts.
-- Consequential writes are performed by the backend service through the
-- governed Business Activation flow; these tables are not a second authority.

CREATE TABLE IF NOT EXISTS business_activation_extractions (
    extraction_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    operator_text TEXT NOT NULL,
    fact_types_in_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
    proposed_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    clarifications JSONB NOT NULL DEFAULT '[]'::jsonb,
    unparsed_text JSONB NOT NULL DEFAULT '[]'::jsonb,
    extraction_confidence NUMERIC(5,4) NOT NULL CHECK (extraction_confidence >= 0 AND extraction_confidence <= 1),
    model_used TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_business_activation_extractions_workspace
    ON business_activation_extractions(workspace_id, extracted_at DESC);

CREATE TABLE IF NOT EXISTS business_activation_facts (
    fact_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL CHECK (fact_type IN ('business_hours', 'refund_policy')),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value JSONB NOT NULL,
    conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence NUMERIC(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source TEXT NOT NULL CHECK (source IN ('operator_message', 'document', 'import', 'api')),
    source_span JSONB NOT NULL,
    asserted_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    asserted_by TEXT NOT NULL,
    superseded_at TIMESTAMPTZ,
    superseded_by TEXT REFERENCES business_activation_facts(fact_id),
    extraction_id TEXT NOT NULL REFERENCES business_activation_extractions(extraction_id),
    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    confirmed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_business_activation_facts_current
    ON business_activation_facts(workspace_id, fact_type, subject, predicate)
    WHERE superseded_at IS NULL AND confirmed = TRUE;

CREATE INDEX IF NOT EXISTS idx_business_activation_facts_extraction
    ON business_activation_facts(extraction_id);

ALTER TABLE business_activation_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_activation_facts ENABLE ROW LEVEL SECURITY;

-- KCPG/backend remains the intended application boundary. RLS is enabled
-- without permissive direct Data API policies, so authenticated browser clients
-- cannot bypass the KCPG workspace/session controls.
