-- Phase 33: close legacy anonymous-access gap.
-- These legacy tables do not carry workspace_id, so the safest default is
-- service-role-only access until a tenant-aware migration is introduced.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['chat_history','contact_profiles','bot_events','replied_comments','invited_users'] LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated', t);
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- No client policies are intentionally created here. With RLS enabled and no
-- policies, anon/authenticated have no row access. service_role continues to
-- bypass RLS for backend operations.
