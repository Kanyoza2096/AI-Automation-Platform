-- Trigger functions use fully-qualified application table references.
-- Pin their search path to pg_catalog so an object in public or a temporary
-- schema cannot shadow a built-in name during trigger execution.

ALTER FUNCTION public.reject_director_mission_event_mutation()
    SET search_path = pg_catalog;

ALTER FUNCTION public.guard_director_mission_insert()
    SET search_path = pg_catalog;

ALTER FUNCTION public.guard_director_mission_state_update()
    SET search_path = pg_catalog;

ALTER FUNCTION public.guard_director_mission_event_insert()
    SET search_path = pg_catalog;
