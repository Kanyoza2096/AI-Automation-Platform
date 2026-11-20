-- Enforce task-to-mission membership for every task lifecycle event.
-- This guard is also defined by migration 176; retained as a separately
-- versioned staging migration because it was already applied there.
CREATE OR REPLACE FUNCTION public.guard_director_mission_event_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $body$
DECLARE
    mission_row public.director_missions%ROWTYPE;
BEGIN
    SELECT * INTO mission_row
      FROM public.director_missions
     WHERE mission_id = NEW.mission_id;
    IF NOT FOUND
       OR mission_row.scope_type <> NEW.scope_type
       OR mission_row.workspace_id IS DISTINCT FROM NEW.workspace_id THEN
        RAISE EXCEPTION 'director_mission_event_scope_mismatch';
    END IF;
    IF NEW.task_id IS NOT NULL AND NOT (mission_row.task_ids ? NEW.task_id) THEN
        RAISE EXCEPTION 'director_mission_event_task_scope_mismatch';
    END IF;
    IF NEW.event_type LIKE 'task.%' AND NEW.task_id IS NULL THEN
        RAISE EXCEPTION 'director_task_event_requires_task_id';
    END IF;
    IF NEW.sequence_number <> mission_row.version + 1 THEN
        RAISE EXCEPTION 'director_mission_event_sequence_mismatch';
    END IF;
    RETURN NEW;
END;
$body$;
