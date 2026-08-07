-- Kanyoza Systems Bot — Supabase schema
-- Run this once in your Supabase project: Dashboard → SQL Editor → New query

-- -----------------------------------------------------------------------
-- chat_history  (conversation memory, persisted per sender)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chat_history (
    id          BIGSERIAL PRIMARY KEY,
    sender_id   TEXT        NOT NULL,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_sender
    ON public.chat_history (sender_id, created_at DESC);

-- -----------------------------------------------------------------------
-- contact_profiles  (user facts / profile data)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.contact_profiles (
    id          BIGSERIAL PRIMARY KEY,
    sender_id   TEXT        NOT NULL,
    fact_key    TEXT        NOT NULL,
    fact_value  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sender_id, fact_key)
);

-- -----------------------------------------------------------------------
-- bot_events  (analytics event log)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bot_events (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT        NOT NULL,
    payload     JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bot_events_type
    ON public.bot_events (event_type, created_at DESC);

-- -----------------------------------------------------------------------
-- replied_comments  (growth service — tracks which comments were replied to)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.replied_comments (
    comment_id  TEXT        PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- invited_users  (growth service — tracks which users received page invites)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.invited_users (
    user_id     TEXT        PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------
-- Row Level Security — disable for service-role access (bot uses service key)
-- -----------------------------------------------------------------------
ALTER TABLE public.chat_history      DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.contact_profiles  DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.bot_events        DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.replied_comments  DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.invited_users     DISABLE ROW LEVEL SECURITY;
