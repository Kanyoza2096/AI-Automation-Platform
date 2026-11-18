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
    submitted_by TEXT NOT NULL CHECK (length(btrim(submitted_by)) > 0),
    source_url TEXT CHECK (source_url IS NULL OR source_url ~ '^https?://[^/[:space:]?#]+([/?#].*)?$'),
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
                AND source_url IS NOT NULL AND source_url ~ '^https?://[^/[:space:]?#]+([/?#].*)?$'
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


-- Make the decision history unavoidable for normal service-role writes.
CREATE OR REPLACE FUNCTION public.record_knowledge_evidence_review_event()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    event_name TEXT;
    event_actor TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        event_name := 'submitted';
        event_actor := NEW.submitted_by;
        INSERT INTO public.knowledge_evidence_review_events (
            case_id, event_type, actor_id, reason, evidence
        ) VALUES (
            NEW.id,
            event_name,
            event_actor,
            'Evidence review case submitted',
            jsonb_build_object(
                'knowledge_entry_id', NEW.knowledge_entry_id,
                'content_sha256', NEW.content_sha256,
                'source_id', NEW.source_id
            )
        );
        RETURN NEW;
    END IF;

    IF NEW.review_status IS DISTINCT FROM OLD.review_status THEN
        event_name := CASE NEW.review_status
            WHEN 'approved' THEN 'approved'
            WHEN 'rejected' THEN 'rejected'
            WHEN 'superseded' THEN 'superseded'
            ELSE 'reopened'
        END;
        event_actor := coalesce(NEW.reviewed_by, NEW.submitted_by);
        INSERT INTO public.knowledge_evidence_review_events (
            case_id, event_type, actor_id, reason, evidence
        ) VALUES (
            NEW.id,
            event_name,
            event_actor,
            NEW.review_notes,
            jsonb_build_object(
                'review_status', NEW.review_status,
                'content_sha256', NEW.content_sha256,
                'source_id', NEW.source_id,
                'source_url', NEW.source_url
            )
        );
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.record_knowledge_evidence_review_event()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS knowledge_evidence_review_case_audit
    ON public.knowledge_evidence_review_cases;
CREATE TRIGGER knowledge_evidence_review_case_audit
    AFTER INSERT OR UPDATE OF review_status
    ON public.knowledge_evidence_review_cases
    FOR EACH ROW
    EXECUTE FUNCTION public.record_knowledge_evidence_review_event();


CREATE OR REPLACE FUNCTION public.touch_knowledge_evidence_review_case()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.touch_knowledge_evidence_review_case()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS knowledge_evidence_review_case_touch
    ON public.knowledge_evidence_review_cases;
CREATE TRIGGER knowledge_evidence_review_case_touch
    BEFORE UPDATE
    ON public.knowledge_evidence_review_cases
    FOR EACH ROW
    EXECUTE FUNCTION public.touch_knowledge_evidence_review_case();


-- Database-enforced binding: a case can only be submitted/approved for the
-- exact current knowledge content and its real tenant. FOR UPDATE serializes
-- the check against concurrent edits to the knowledge row.
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE OR REPLACE FUNCTION public.guard_knowledge_evidence_review_case()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    current_content TEXT;
    current_brand_id TEXT;
    current_workspace_id TEXT;
    current_hash TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.knowledge_entry_id IS DISTINCT FROM OLD.knowledge_entry_id
           OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
           OR NEW.brand_id IS DISTINCT FROM OLD.brand_id
           OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
           OR NEW.metadata_snapshot IS DISTINCT FROM OLD.metadata_snapshot
           OR NEW.claim_scope IS DISTINCT FROM OLD.claim_scope
           OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Evidence review case identity and snapshot are immutable';
        END IF;
        IF OLD.review_status = 'approved' THEN
            IF NEW.review_status <> 'superseded'
               OR NEW.source_id IS DISTINCT FROM OLD.source_id
               OR NEW.source_url IS DISTINCT FROM OLD.source_url
               OR NEW.source_title IS DISTINCT FROM OLD.source_title
               OR NEW.source_type IS DISTINCT FROM OLD.source_type
               OR NEW.trust_class IS DISTINCT FROM OLD.trust_class THEN
                RAISE EXCEPTION 'Approved evidence review cases are immutable';
            END IF;
        END IF;
        IF OLD.review_status IN ('rejected', 'superseded')
           AND NEW.review_status <> 'pending' THEN
            RAISE EXCEPTION 'Only rejected or superseded cases may be reopened';
        END IF;
    END IF;

    IF NEW.review_status = 'approved' OR TG_OP = 'INSERT' THEN
        SELECT content, brand_id, workspace_id
          INTO current_content, current_brand_id, current_workspace_id
          FROM public.knowledge_entries
         WHERE id = NEW.knowledge_entry_id
         FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Knowledge entry does not exist';
        END IF;
        IF current_brand_id IS DISTINCT FROM NEW.brand_id
           OR current_workspace_id IS DISTINCT FROM NEW.workspace_id THEN
            RAISE EXCEPTION 'Evidence review tenant binding does not match knowledge entry';
        END IF;

        current_hash := encode(
            digest(convert_to(current_content, 'UTF8'), 'sha256'),
            'hex'
        );
        IF current_hash IS DISTINCT FROM NEW.content_sha256 THEN
            RAISE EXCEPTION 'Knowledge content changed; evidence review case is stale';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.guard_knowledge_evidence_review_case()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS knowledge_evidence_review_case_guard
    ON public.knowledge_evidence_review_cases;
CREATE TRIGGER knowledge_evidence_review_case_guard
    BEFORE INSERT OR UPDATE
    ON public.knowledge_evidence_review_cases
    FOR EACH ROW
    EXECUTE FUNCTION public.guard_knowledge_evidence_review_case();
