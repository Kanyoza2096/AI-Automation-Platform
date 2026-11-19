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
