-- Business Activation Phase 5: durable Knowledge Plane confirmation/versioning.
-- The KCPG/backend service is the application boundary; browser clients do not
-- receive direct write authority to these tables.

ALTER TABLE business_activation_extractions
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS confirmed_by TEXT,
    ADD COLUMN IF NOT EXISTS confirmed_fact_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_activation_current_fact
    ON business_activation_facts(workspace_id, fact_type, subject, predicate, conditions)
    WHERE superseded_at IS NULL AND confirmed = TRUE;

CREATE OR REPLACE FUNCTION confirm_business_activation_facts(
    p_workspace_id TEXT,
    p_extraction_id TEXT,
    p_proposed_fact_ids TEXT[],
    p_asserted_by TEXT
)
RETURNS SETOF business_activation_facts
LANGUAGE plpgsql
AS $$
DECLARE
    existing_count INTEGER;
    selected RECORD;
    new_row business_activation_facts%ROWTYPE;
    new_ids TEXT[] := '{}';
    now_utc TIMESTAMPTZ := timezone('utc', now());
BEGIN
    SELECT count(*) INTO existing_count
    FROM business_activation_facts
    WHERE workspace_id = p_workspace_id
      AND extraction_id = p_extraction_id
      AND confirmed = TRUE;

    IF existing_count > 0 THEN
        RETURN QUERY
        SELECT * FROM business_activation_facts
        WHERE workspace_id = p_workspace_id
          AND extraction_id = p_extraction_id
          AND confirmed = TRUE
        ORDER BY confirmed_at, fact_id;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM business_activation_extractions
        WHERE extraction_id = p_extraction_id AND workspace_id = p_workspace_id
    ) THEN
        RAISE EXCEPTION 'extraction_not_found';
    END IF;

    FOR selected IN
        SELECT * FROM business_activation_facts
        WHERE workspace_id = p_workspace_id
          AND extraction_id = p_extraction_id
          AND fact_id = ANY(p_proposed_fact_ids)
          AND confirmed = FALSE
        ORDER BY fact_id
    LOOP
        UPDATE business_activation_facts old
           SET superseded_at = now_utc, superseded_by = selected.fact_id
         WHERE old.workspace_id = p_workspace_id
           AND old.fact_type = selected.fact_type
           AND old.subject = selected.subject
           AND old.predicate = selected.predicate
           AND old.conditions = selected.conditions
           AND old.confirmed = TRUE
           AND old.superseded_at IS NULL;

        UPDATE business_activation_facts
           SET confirmed = TRUE, confirmed_at = now_utc, asserted_by = p_asserted_by
         WHERE fact_id = selected.fact_id
           AND workspace_id = p_workspace_id
           AND confirmed = FALSE;

        new_ids := array_append(new_ids, selected.fact_id);
    END LOOP;

    IF coalesce(array_length(new_ids, 1), 0) <> coalesce(array_length(p_proposed_fact_ids, 1), 0) THEN
        RAISE EXCEPTION 'proposed_fact_not_found';
    END IF;

    UPDATE business_activation_extractions
       SET confirmed_at = now_utc,
           confirmed_by = p_asserted_by,
           confirmed_fact_ids = to_jsonb(new_ids)
     WHERE extraction_id = p_extraction_id AND workspace_id = p_workspace_id;

    RETURN QUERY
    SELECT * FROM business_activation_facts
     WHERE workspace_id = p_workspace_id
       AND fact_id = ANY(new_ids)
     ORDER BY confirmed_at, fact_id;
END;
$$;

ALTER TABLE business_activation_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_activation_facts ENABLE ROW LEVEL SECURITY;
