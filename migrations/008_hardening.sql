-- SQL Migration: 008_hardening.sql
-- Run in Supabase SQL editor to create required tables and indexes.

-- 1. Create api_keys table if not exists (for token manager & authentication)
CREATE TABLE IF NOT EXISTS public.api_keys (
    id TEXT PRIMARY KEY,
    label TEXT,
    prefix TEXT,
    hashed TEXT UNIQUE,
    key_hash TEXT UNIQUE,
    tenant_id TEXT,
    workspace_id TEXT,
    permissions JSONB DEFAULT '["read"]'::jsonb,
    expires_at TIMESTAMP WITH TIME ZONE,
    revoked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    last_used_at TIMESTAMP WITH TIME ZONE
);

-- 2. Create audit_logs table if not exists (for persistent audit trail)
CREATE TABLE IF NOT EXISTS public.audit_logs (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    correlation_id TEXT,
    "user" TEXT,
    action TEXT NOT NULL,
    resource TEXT,
    status TEXT,
    detail TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 3. Create missing indexes for query optimizations
CREATE INDEX IF NOT EXISTS idx_social_accounts_workspace ON public.social_accounts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_social_accounts_platform ON public.social_accounts(platform);
CREATE INDEX IF NOT EXISTS idx_brands_workspace ON public.brands(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ai_profiles_workspace ON public.ai_profiles(workspace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_brand ON public.knowledge_entries(brand_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hashed ON public.api_keys(hashed);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON public.audit_logs(timestamp DESC);

-- 4. Enable permissions for service_role and anon
GRANT ALL ON public.api_keys TO service_role, anon;
GRANT ALL ON public.audit_logs TO service_role, anon;
