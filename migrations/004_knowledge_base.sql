-- Phase 4: Knowledge Base Migration

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id TEXT PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    brand_id TEXT REFERENCES brands(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'text', -- text, url, file, topic
    embedding_json JSONB DEFAULT '[]'::jsonb, -- TF-IDF sparse vector or dense vector values
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_entries_brand ON knowledge_entries(brand_id);
