-- V68.15 Learning tenant integrity.
-- Learning may inform decisions, so its evidence chain must remain tenant-bound.

CREATE OR REPLACE FUNCTION kdo_assert_learning_evidence_workspace()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  source_id text;
  source_workspace uuid;
BEGIN
  IF NEW.evidence ? 'source_signal_ids' THEN
    FOR source_id IN
      SELECT value::text FROM jsonb_array_elements_text(NEW.evidence->'source_signal_ids') AS value
    LOOP
      SELECT workspace_id INTO source_workspace
      FROM business_outcome_learning_signals
      WHERE signal_id = source_id;

      IF source_workspace IS NULL THEN
        RAISE EXCEPTION 'learning references unknown source signal %', source_id;
      END IF;
      IF source_workspace <> NEW.workspace_id THEN
        RAISE EXCEPTION 'learning source signal workspace mismatch for %', source_id;
      END IF;
    END LOOP;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_learning_evidence_workspace ON business_learning_governance;
CREATE TRIGGER trg_learning_evidence_workspace
BEFORE INSERT OR UPDATE OF workspace_id, evidence
ON business_learning_governance
FOR EACH ROW EXECUTE FUNCTION kdo_assert_learning_evidence_workspace();

CREATE OR REPLACE FUNCTION kdo_assert_learning_context_knowledge_workspace()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  item jsonb;
  kid text;
  source_workspace uuid;
BEGIN
  FOR item IN SELECT value FROM jsonb_array_elements(NEW.knowledge)
  LOOP
    kid := item->>'knowledge_id';
    IF kid IS NULL OR kid = '' THEN
      RAISE EXCEPTION 'learning decision context contains knowledge without knowledge_id';
    END IF;

    SELECT workspace_id INTO source_workspace
    FROM business_learning_governance
    WHERE knowledge_id = kid;

    IF source_workspace IS NULL THEN
      RAISE EXCEPTION 'learning decision context references unknown knowledge %', kid;
    END IF;
    IF source_workspace <> NEW.workspace_id THEN
      RAISE EXCEPTION 'learning decision context knowledge workspace mismatch for %', kid;
    END IF;
  END LOOP;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_learning_context_knowledge_workspace ON business_learning_decision_context;
CREATE TRIGGER trg_learning_context_knowledge_workspace
BEFORE INSERT OR UPDATE OF workspace_id, knowledge
ON business_learning_decision_context
FOR EACH ROW EXECUTE FUNCTION kdo_assert_learning_context_knowledge_workspace();
