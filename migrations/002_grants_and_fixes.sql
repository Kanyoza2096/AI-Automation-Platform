-- Kanyoza Systems Bot — grants + schema fixes
-- Run in Supabase: Dashboard → SQL Editor → New query

-- -----------------------------------------------------------------------
-- 1. Grant full access to service_role and anon on all bot tables
-- -----------------------------------------------------------------------
GRANT ALL ON public.chat_history      TO service_role, anon;
GRANT ALL ON public.contact_profiles  TO service_role, anon;
GRANT ALL ON public.bot_events        TO service_role, anon;
GRANT ALL ON public.replied_comments  TO service_role, anon;
GRANT ALL ON public.invited_users     TO service_role, anon;

-- Sequences (needed for BIGSERIAL inserts)
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role, anon;

-- -----------------------------------------------------------------------
-- 2. Fix chat_history — add missing columns if the table pre-existed
--    with a different schema (e.g. "message" instead of "content")
-- -----------------------------------------------------------------------
ALTER TABLE public.chat_history ADD COLUMN IF NOT EXISTS sender_id  TEXT;
ALTER TABLE public.chat_history ADD COLUMN IF NOT EXISTS role       TEXT;
ALTER TABLE public.chat_history ADD COLUMN IF NOT EXISTS content    TEXT;
ALTER TABLE public.chat_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

-- -----------------------------------------------------------------------
-- 3. Re-confirm RLS is off (harmless if already off)
-- -----------------------------------------------------------------------
ALTER TABLE public.chat_history      DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.contact_profiles  DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.bot_events        DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.replied_comments  DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.invited_users     DISABLE ROW LEVEL SECURITY;
