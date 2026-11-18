-- Explicit, transactional promotion from a human-approved evidence case
-- into the existing deterministic publishing evidence gate.
-- This migration is additive and is NOT applied to any database by this PR.

CREATE TABLE IF NOT EXISTS public.knowledge_evidence_promotion_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id UUID NOT NULL REFERENCES public.knowledge_evidence_review_cases(id) ON DELETE RESTRICT,
    knowledge_entry_id TEXT NOT NULL REFERENCES public.knowledge_entries(id) ON DELETE RESTRICT,
    workspace_id TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('promoted', 'revoked')),
    actor_id TEXT NOT NULL CHECK (length(btrim(actor_id)) > 0),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL CHECK (source_url ~ '^https?://[^/[:space:]?#]+([/?#].*)?$'),
    metadata_before JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_after JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT knowledge_evidence_promotion_case_event_unique
        UNIQUE (case_id, event_type)
);

CREATE INDEX IF NOT EXISTS knowledge_evidence_promotion_entry_idx
    ON public.knowledge_evidence_promotion_events (knowledge_entry_id, created_at DESC);

ALTER TABLE public.knowledge_evidence_promotion_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.knowledge_evidence_promotion_events
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.knowledge_evidence_promotion_events TO service_role;

COMMENT ON TABLE public.knowledge_evidence_promotion_events IS
    'Append-only audit of approved knowledge evidence promotion and automatic revocation after content edits.';

CREATE OR REPLACE FUNCTION public.promote_approved_knowledge_evidence(
    p_case_id UUID,
    p_actor_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    review_case public.knowledge_evidence_review_cases%ROWTYPE;
    entry_content TEXT;
    entry_brand_id TEXT;
    entry_workspace_id TEXT;
    metadata_before JSONB;
    metadata_after JSONB;
    current_hash TEXT;
    existing_event public.knowledge_evidence_promotion_events%ROWTYPE;
    created_event public.knowledge_evidence_promotion_events%ROWTYPE;
BEGIN
    IF p_actor_id IS NULL OR length(btrim(p_actor_id)) = 0 THEN
        RAISE EXCEPTION 'A reviewer actor identity is required';
    END IF;

    SELECT * INTO review_case
      FROM public.knowledge_evidence_review_cases
     WHERE id = p_case_id
     FOR UPDATE;
    IF NOT FOUND OR review_case.review_status <> 'approved' THEN
        RAISE EXCEPTION 'Only an approved evidence review case can be promoted';
    END IF;

    SELECT * INTO existing_event
      FROM public.knowledge_evidence_promotion_events
     WHERE case_id = p_case_id AND event_type = 'promoted';
    IF FOUND THEN
        RETURN jsonb_build_object(
            'promoted', true,
            'already_promoted', true,
            'promotion_event_id', existing_event.id,
            'knowledge_entry_id', existing_event.knowledge_entry_id
        );
    END IF;

    SELECT content, brand_id, workspace_id, metadata
      INTO entry_content, entry_brand_id, entry_workspace_id, metadata_before
      FROM public.knowledge_entries
     WHERE id = review_case.knowledge_entry_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Knowledge entry no longer exists';
    END IF;
    IF entry_brand_id IS DISTINCT FROM review_case.brand_id
       OR entry_workspace_id IS DISTINCT FROM review_case.workspace_id THEN
        RAISE EXCEPTION 'Evidence review tenant binding no longer matches';
    END IF;

    current_hash := encode(
        extensions.digest(convert_to(entry_content, 'UTF8'), 'sha256'),
        'hex'
    );
    IF current_hash IS DISTINCT FROM review_case.content_sha256 THEN
        RAISE EXCEPTION 'Knowledge content changed after review; promotion refused';
    END IF;
    IF review_case.source_id IS NULL
       OR review_case.source_id LIKE 'unverified-entry:%'
       OR review_case.source_url IS NULL
       OR review_case.source_url !~ '^https?://[^/[:space:]?#]+([/?#].*)?$' THEN
        RAISE EXCEPTION 'Approved case is missing stable source provenance';
    END IF;

    IF jsonb_typeof(metadata_before) <> 'object' OR metadata_before IS NULL THEN
        metadata_before := '{}'::jsonb;
    END IF;
    metadata_after := metadata_before || jsonb_build_object(
        'verified', true,
        'source_id', review_case.source_id,
        'source_url', review_case.source_url,
        'evidence_review_case_id', review_case.id::text,
        'evidence_content_sha256', review_case.content_sha256,
        'evidence_claim_scope', review_case.claim_scope,
        'evidence_reviewed_by', review_case.reviewed_by,
        'evidence_reviewed_at', review_case.reviewed_at
    );

    UPDATE public.knowledge_entries
       SET metadata = metadata_after,
           updated_at = NOW()
     WHERE id = review_case.knowledge_entry_id;

    INSERT INTO public.knowledge_evidence_promotion_events (
        case_id, knowledge_entry_id, workspace_id, brand_id, event_type,
        actor_id, content_sha256, source_id, source_url,
        metadata_before, metadata_after, reason
    ) VALUES (
        review_case.id, review_case.knowledge_entry_id, review_case.workspace_id,
        review_case.brand_id, 'promoted', p_actor_id, review_case.content_sha256,
        review_case.source_id, review_case.source_url, metadata_before,
        metadata_after, 'Human-approved evidence promoted into publishing validator'
    )
    RETURNING * INTO created_event;

    RETURN jsonb_build_object(
        'promoted', true,
        'already_promoted', false,
        'promotion_event_id', created_event.id,
        'knowledge_entry_id', created_event.knowledge_entry_id,
        'source_id', created_event.source_id,
        'content_sha256', created_event.content_sha256
    );
END;
$$;

REVOKE ALL ON FUNCTION public.promote_approved_knowledge_evidence(UUID, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.promote_approved_knowledge_evidence(UUID, TEXT)
    TO service_role;

-- If a promoted entry's content changes, invalidate its verified flag and
-- supersede its review case in the same transaction. The original source
-- decision remains auditable but cannot bless the edited text.
CREATE OR REPLACE FUNCTION public.invalidate_knowledge_evidence_on_content_change()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.content IS DISTINCT FROM OLD.content
       AND COALESCE(OLD.metadata->>'evidence_review_case_id', '') <> ''
       AND OLD.metadata->>'verified' = 'true' THEN
        NEW.metadata := COALESCE(NEW.metadata, '{}'::jsonb) || jsonb_build_object(
            'verified', false,
            'evidence_status', 'stale',
            'evidence_invalidated_at', NOW(),
            'evidence_invalidation_reason', 'knowledge_content_changed'
        );
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.invalidate_knowledge_evidence_on_content_change()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS knowledge_evidence_content_invalidate
    ON public.knowledge_entries;
CREATE TRIGGER knowledge_evidence_content_invalidate
    BEFORE UPDATE OF content ON public.knowledge_entries
    FOR EACH ROW
    EXECUTE FUNCTION public.invalidate_knowledge_evidence_on_content_change();

CREATE OR REPLACE FUNCTION public.audit_knowledge_evidence_content_change()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, extensions, pg_temp
AS $$
DECLARE
    current_hash TEXT;
    review_case public.knowledge_evidence_review_cases%ROWTYPE;
    promoted_event public.knowledge_evidence_promotion_events%ROWTYPE;
BEGIN
    IF NEW.content IS NOT DISTINCT FROM OLD.content THEN
        RETURN NEW;
    END IF;

    current_hash := encode(
        extensions.digest(convert_to(NEW.content, 'UTF8'), 'sha256'),
        'hex'
    );

    FOR review_case IN
        SELECT * FROM public.knowledge_evidence_review_cases
         WHERE knowledge_entry_id = NEW.id
           AND content_sha256 IS DISTINCT FROM current_hash
           AND review_status IN ('pending', 'approved')
         FOR UPDATE
    LOOP
        UPDATE public.knowledge_evidence_review_cases
           SET review_status = 'superseded',
               reviewed_by = 'system:knowledge-content-change',
               reviewed_at = NOW(),
               review_notes = 'Knowledge content changed; prior source approval no longer applies.'
         WHERE id = review_case.id;
    END LOOP;

    FOR promoted_event IN
        SELECT * FROM public.knowledge_evidence_promotion_events
         WHERE knowledge_entry_id = NEW.id
           AND event_type = 'promoted'
           AND content_sha256 IS DISTINCT FROM current_hash
    LOOP
        INSERT INTO public.knowledge_evidence_promotion_events (
            case_id, knowledge_entry_id, workspace_id, brand_id, event_type,
            actor_id, content_sha256, source_id, source_url,
            metadata_before, metadata_after, reason
        ) VALUES (
            promoted_event.case_id, promoted_event.knowledge_entry_id,
            promoted_event.workspace_id, promoted_event.brand_id, 'revoked',
            'system:knowledge-content-change', promoted_event.content_sha256,
            promoted_event.source_id, promoted_event.source_url,
            COALESCE(OLD.metadata, '{}'::jsonb), COALESCE(NEW.metadata, '{}'::jsonb),
            'Promoted evidence invalidated because knowledge content changed'
        )
        ON CONFLICT (case_id, event_type) DO NOTHING;
    END LOOP;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.audit_knowledge_evidence_content_change()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS knowledge_evidence_content_change_audit
    ON public.knowledge_entries;
CREATE TRIGGER knowledge_evidence_content_change_audit
    AFTER UPDATE OF content ON public.knowledge_entries
    FOR EACH ROW
    EXECUTE FUNCTION public.audit_knowledge_evidence_content_change();
