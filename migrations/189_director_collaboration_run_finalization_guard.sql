-- Make finalized Director collaboration runs tamper-evident at the row boundary.
-- Progress updates are allowed only while status remains 'running'. A single
-- transition to completed/partial/failed seals the record; terminal rows cannot
-- be updated or deleted by the service role.

CREATE OR REPLACE FUNCTION public.guard_director_collaboration_run_finalization()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'director_collaboration_run_delete_forbidden';
    END IF;

    IF OLD.status <> 'running' THEN
        RAISE EXCEPTION 'director_collaboration_run_finalized_immutable';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
       OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.objective IS DISTINCT FROM OLD.objective
       OR NEW.requested_engines IS DISTINCT FROM OLD.requested_engines
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'director_collaboration_run_identity_immutable';
    END IF;

    IF NEW.status = 'running' THEN
        IF NEW.completed_at IS NOT NULL THEN
            RAISE EXCEPTION 'director_collaboration_run_running_has_completion_time';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status NOT IN ('completed', 'partial', 'failed')
       OR NEW.completed_at IS NULL THEN
        RAISE EXCEPTION 'director_collaboration_run_terminal_state_invalid';
    END IF;

    IF NEW.status = 'completed' THEN
        IF jsonb_typeof(NEW.consultations) IS DISTINCT FROM 'array'
           OR EXISTS (
               SELECT 1
                 FROM jsonb_array_elements(NEW.consultations) AS consultation(item)
                WHERE consultation.item->>'status' IS DISTINCT FROM 'completed'
                   OR COALESCE((consultation.item->'sources'->>'partial')::BOOLEAN, FALSE)
           ) THEN
            RAISE EXCEPTION 'director_collaboration_run_completed_consultations_incomplete';
        END IF;

        IF NEW.evidence_summary #>> '{comparison,status}' IS DISTINCT FROM 'available'
           OR NEW.evidence_summary #>> '{recommendation,status}' IS DISTINCT FROM 'available' THEN
            RAISE EXCEPTION 'director_collaboration_run_completed_synthesis_unavailable';
        END IF;
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_guard_director_collaboration_run_finalization
    ON public.director_collaboration_runs;

CREATE TRIGGER trg_guard_director_collaboration_run_finalization
BEFORE UPDATE OR DELETE ON public.director_collaboration_runs
FOR EACH ROW
EXECUTE FUNCTION public.guard_director_collaboration_run_finalization();

COMMENT ON FUNCTION public.guard_director_collaboration_run_finalization() IS
    'Allows live progress updates only while a collaboration is running; seals terminal records and prevents service-role mutation or deletion after finalization.';
