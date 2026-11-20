-- Tighten mission state/event mutation to the reviewed RPC boundary.
-- SECURITY DEFINER is intentionally limited to two fixed-search-path functions;
-- only service_role may execute them. Browser roles and service_role cannot
-- mutate the mission/event tables directly.

REVOKE ALL ON TABLE public.director_missions FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON TABLE public.director_mission_events FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT ON TABLE public.director_missions TO service_role;
GRANT SELECT ON TABLE public.director_mission_events TO service_role;

ALTER FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) SECURITY DEFINER;

ALTER FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
    SECURITY DEFINER;

COMMENT ON FUNCTION public.append_director_mission_event(
    TEXT, TEXT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, JSONB, TEXT, TEXT, JSONB
) IS
    'Privileged, fixed-search-path RPC. Appends one mission event and advances its state atomically; callable only by service_role.';
COMMENT ON FUNCTION public.stage_director_mission_v2(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) IS
    'Privileged, fixed-search-path RPC. Performs governed workspace mission admission and creates the mission aggregate/event atomically; callable only by service_role.';
