-- Director task records are backend-owned control-plane state.
-- The legacy policy allowed workspace_id IS NULL rows to be read by anon,
-- and anon/authenticated also held TRUNCATE privileges (RLS does not cover
-- TRUNCATE). The browser must use the authenticated KCPG -> backend path.

ALTER TABLE public.director_tasks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS director_tasks_policy ON public.director_tasks;

REVOKE ALL ON TABLE public.director_tasks
    FROM PUBLIC, anon, authenticated;

-- The backend service role retains the CRUD operations used by DirectorStore.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.director_tasks
    TO service_role;

DO $$
BEGIN
    IF has_table_privilege('anon', 'public.director_tasks', 'SELECT')
       OR has_table_privilege('anon', 'public.director_tasks', 'INSERT')
       OR has_table_privilege('anon', 'public.director_tasks', 'UPDATE')
       OR has_table_privilege('anon', 'public.director_tasks', 'DELETE')
       OR has_table_privilege('anon', 'public.director_tasks', 'TRUNCATE') THEN
        RAISE EXCEPTION 'director_tasks_anon_privilege_revoke_failed';
    END IF;

    IF has_table_privilege('authenticated', 'public.director_tasks', 'SELECT')
       OR has_table_privilege('authenticated', 'public.director_tasks', 'INSERT')
       OR has_table_privilege('authenticated', 'public.director_tasks', 'UPDATE')
       OR has_table_privilege('authenticated', 'public.director_tasks', 'DELETE')
       OR has_table_privilege('authenticated', 'public.director_tasks', 'TRUNCATE') THEN
        RAISE EXCEPTION 'director_tasks_authenticated_privilege_revoke_failed';
    END IF;

    IF NOT has_table_privilege('service_role', 'public.director_tasks', 'SELECT')
       OR NOT has_table_privilege('service_role', 'public.director_tasks', 'INSERT')
       OR NOT has_table_privilege('service_role', 'public.director_tasks', 'UPDATE')
       OR NOT has_table_privilege('service_role', 'public.director_tasks', 'DELETE') THEN
        RAISE EXCEPTION 'director_tasks_service_role_crud_missing';
    END IF;
END;
$$;

COMMENT ON TABLE public.director_tasks IS
    'Backend-only Director task state. Browser access is mediated by authenticated KCPG and the backend service role.';
