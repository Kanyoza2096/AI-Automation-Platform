-- Persistent, tenant-isolated AI chat history.
-- Browser roles receive no direct table access; the authenticated backend
-- derives workspace ownership from its verified request context.
CREATE TABLE IF NOT EXISTS public.ai_conversations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    engine TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ai_conversations_engine_check
        CHECK (engine IN ('general', 'director', 'guardian', 'council', 'ai_brain', 'content')),
    CONSTRAINT ai_conversations_title_length
        CHECK (char_length(title) BETWEEN 1 AND 160),
    CONSTRAINT ai_conversations_id_workspace_unique
        UNIQUE (id, workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_workspace_updated
    ON public.ai_conversations (workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.ai_conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    tokens_used INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ai_conversation_messages_role_check
        CHECK (role IN ('user', 'assistant')),
    CONSTRAINT ai_conversation_messages_content_length
        CHECK (char_length(content) BETWEEN 1 AND 20000),
    CONSTRAINT ai_conversation_messages_tokens_nonnegative
        CHECK (tokens_used IS NULL OR tokens_used >= 0),
    CONSTRAINT ai_conversation_messages_conversation_workspace_fk
        FOREIGN KEY (conversation_id, workspace_id)
        REFERENCES public.ai_conversations (id, workspace_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ai_conversation_messages_workspace_conversation_created
    ON public.ai_conversation_messages (workspace_id, conversation_id, created_at ASC);

ALTER TABLE public.ai_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_conversation_messages ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.ai_conversations FROM PUBLIC, anon, authenticated;
REVOKE ALL ON TABLE public.ai_conversation_messages FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.ai_conversations TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.ai_conversation_messages TO service_role;

COMMENT ON TABLE public.ai_conversations IS
    'Workspace-scoped AI conversations. Accessed only by the authenticated backend service role.';
COMMENT ON TABLE public.ai_conversation_messages IS
    'Workspace-scoped AI conversation turns with composite ownership FK; no browser table access.';
