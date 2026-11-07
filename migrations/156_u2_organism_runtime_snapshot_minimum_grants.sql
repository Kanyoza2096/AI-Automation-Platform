-- U2: keep the runtime snapshot table on a least-privilege service-role grant.
-- Supabase default privileges may have added broader table grants at creation.
REVOKE ALL PRIVILEGES ON TABLE public.organism_runtime_snapshots FROM service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.organism_runtime_snapshots TO service_role;
