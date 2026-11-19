-- Autonomous knowledge evidence review state and audit metadata.
-- Additive only: no existing cases or knowledge entries are approved or promoted.
ALTER TABLE public.knowledge_evidence_review_cases
    DROP CONSTRAINT IF EXISTS knowledge_evidence_review_cases_review_status_check;
ALTER TABLE public.knowledge_evidence_review_cases
    ADD CONSTRAINT knowledge_evidence_review_cases_review_status_check
    CHECK (review_status IN ('pending', 'approved', 'rejected', 'superseded', 'quarantined'));

ALTER TABLE public.knowledge_evidence_review_events
    DROP CONSTRAINT IF EXISTS knowledge_evidence_review_events_event_type_check;
ALTER TABLE public.knowledge_evidence_review_events
    ADD CONSTRAINT knowledge_evidence_review_events_event_type_check
    CHECK (event_type IN ('submitted', 'approved', 'rejected', 'reopened', 'superseded', 'quarantined'));

ALTER TABLE public.knowledge_evidence_review_cases
    ADD COLUMN IF NOT EXISTS review_mode TEXT NOT NULL DEFAULT 'human'
        CHECK (review_mode IN ('human', 'autonomous')),
    ADD COLUMN IF NOT EXISTS review_confidence NUMERIC(5,4)
        CHECK (review_confidence IS NULL OR (review_confidence >= 0 AND review_confidence <= 1)),
    ADD COLUMN IF NOT EXISTS review_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(review_evidence) = 'object');

COMMENT ON COLUMN public.knowledge_evidence_review_cases.review_mode IS
    'Whether the decision was made by a verified human or the bounded KDO autonomous reviewer.';
COMMENT ON COLUMN public.knowledge_evidence_review_cases.review_confidence IS
    'Calibrated model confidence recorded for autonomous review; never sufficient by itself to approve.';
COMMENT ON COLUMN public.knowledge_evidence_review_cases.review_evidence IS
    'Structured evidence snapshot and claim-level adjudication used for the decision.';

CREATE OR REPLACE FUNCTION public.record_knowledge_evidence_review_event()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    event_name TEXT;
    event_actor TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.knowledge_evidence_review_events (
            case_id, event_type, actor_id, reason, evidence
        ) VALUES (
            NEW.id, 'submitted', NEW.submitted_by,
            'Evidence review case submitted',
            jsonb_build_object('knowledge_entry_id', NEW.knowledge_entry_id,
                'content_sha256', NEW.content_sha256, 'source_id', NEW.source_id)
        );
        RETURN NEW;
    END IF;

    IF NEW.review_status IS DISTINCT FROM OLD.review_status THEN
        event_name := CASE NEW.review_status
            WHEN 'approved' THEN 'approved'
            WHEN 'rejected' THEN 'rejected'
            WHEN 'quarantined' THEN 'quarantined'
            WHEN 'superseded' THEN 'superseded'
            ELSE 'reopened'
        END;
        event_actor := coalesce(NEW.reviewed_by, NEW.submitted_by);
        INSERT INTO public.knowledge_evidence_review_events (
            case_id, event_type, actor_id, reason, evidence
        ) VALUES (
            NEW.id, event_name, event_actor, NEW.review_notes,
            jsonb_build_object(
                'review_status', NEW.review_status,
                'content_sha256', NEW.content_sha256,
                'source_id', NEW.source_id,
                'source_url', NEW.source_url,
                'review_mode', NEW.review_mode,
                'review_confidence', NEW.review_confidence,
                'review_evidence', NEW.review_evidence
            )
        );
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.record_knowledge_evidence_review_event()
    FROM PUBLIC, anon, authenticated;

-- A quarantined case may be retried when trusted source provenance becomes
-- available or the adjudicator implementation is improved. The immutable
-- content/tenant snapshot remains unchanged; the new decision is audited.
CREATE OR REPLACE FUNCTION public.guard_knowledge_evidence_review_case()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
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
               OR NEW.trust_class IS DISTINCT FROM OLD.trust_class
               OR NEW.review_mode IS DISTINCT FROM OLD.review_mode
               OR NEW.review_confidence IS DISTINCT FROM OLD.review_confidence
               OR NEW.review_evidence IS DISTINCT FROM OLD.review_evidence THEN
                RAISE EXCEPTION 'Approved evidence review cases are immutable';
            END IF;
        ELSIF OLD.review_status = 'pending' THEN
            IF NEW.review_status = 'pending' THEN
                RAISE EXCEPTION 'Pending evidence cases may only change through a decision or supersession';
            END IF;
        ELSIF OLD.review_status IN ('rejected', 'superseded', 'quarantined') THEN
            IF NEW.review_status <> 'pending' THEN
                RAISE EXCEPTION 'Only rejected, superseded, or quarantined cases may be reopened';
            END IF;
        END IF;
        IF NEW.review_status IN ('approved', 'rejected', 'superseded', 'quarantined')
           AND (NEW.reviewed_by IS NULL OR length(btrim(NEW.reviewed_by)) = 0 OR NEW.reviewed_at IS NULL) THEN
            RAISE EXCEPTION 'Evidence review decisions require reviewer identity and timestamp';
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
            extensions.digest(convert_to(current_content, 'UTF8'), 'sha256'),
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
