-- Governed review queue for knowledge-base evidence.
-- This migration creates review records only. It never marks knowledge_entries
-- as verified and does not alter any existing knowledge or publishing job.
CREATE TABLE IF NOT EXISTS public.knowledge_evidence_review_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_entry_id TEXT NOT NULL
        REFERENCES public.knowledge_entries(id) ON DELETE CASCADE,
    workspace_id TEXT,
    brand_id TEXT,
    source_id TEXT NOT NULL,
    source_url TEXT,
    source_title TEXT,
    source_type TEXT,
    trust_class TEXT,
    claim_scope TEXT NOT NULL DEFAULT 'empirical_claim_support',
    content_sha256 TEXT NOT NULL
        CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    metadata_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata_snapshot) = 'object'),
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'approved', 'rejected', 'superseded')),
    review_notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT knowledge_evidence_review_approval_requires_auditor
        CHECK (
            review_status <> 'approved'
            OR (
                reviewed_by IS NOT NULL AND length(btrim(reviewed_by)) > 0
                AND reviewed_at IS NOT NULL
                AND source_id IS NOT NULL AND length(btrim(source_id)) > 0
                AND source_url IS NOT NULL AND length(btrim(source_url)) > 0
            )
        ),
    CONSTRAINT knowledge_evidence_review_decision_has_reviewer
        CHECK (
            review_status NOT IN ('approved', 'rejected')
            OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
        ),
    CONSTRAINT knowledge_evidence_review_case_content_unique
        UNIQUE (knowledge_entry_id, content_sha256)
);

CREATE INDEX IF NOT EXISTS knowledge_evidence_review_cases_queue_idx
    ON public.knowledge_evidence_review_cases (review_status, created_at ASC);
CREATE INDEX IF NOT EXISTS knowledge_evidence_review_cases_brand_idx
    ON public.knowledge_evidence_review_cases (brand_id, review_status);

-- Append-only record of each review decision/change for auditability.
CREATE TABLE IF NOT EXISTS public.knowledge_evidence_review_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL
        REFERENCES public.knowledge_evidence_review_cases(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('submitted', 'approved', 'rejected', 'reopened', 'superseded')),
    actor_id TEXT NOT NULL CHECK (length(btrim(actor_id)) > 0),
    reason TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(evidence) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS knowledge_evidence_review_events_case_idx
    ON public.knowledge_evidence_review_events (case_id, created_at DESC);

ALTER TABLE public.knowledge_evidence_review_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_evidence_review_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.knowledge_evidence_review_cases
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.knowledge_evidence_review_events
    FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT, UPDATE ON TABLE public.knowledge_evidence_review_cases
    TO service_role;
GRANT SELECT, INSERT ON TABLE public.knowledge_evidence_review_events
    TO service_role;

COMMENT ON TABLE public.knowledge_evidence_review_cases IS
    'Service-role-only review queue for evidence candidates. Approval records are not automatically promoted to knowledge_entries.metadata.verified.';
COMMENT ON TABLE public.knowledge_evidence_review_events IS
    'Append-only audit trail for evidence review decisions. Only service_role may append events.';
COMMENT ON COLUMN public.knowledge_evidence_review_cases.content_sha256 IS
    'SHA-256 of the exact knowledge entry content reviewed; any content change requires a new review case.';
