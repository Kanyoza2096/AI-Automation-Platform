-- Phase 1: Foundation & Domain Models Migration

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    owner_email TEXT,
    plan TEXT DEFAULT 'free',
    status TEXT DEFAULT 'active',
    settings JSONB DEFAULT '{}'::jsonb,
    max_brands INTEGER DEFAULT 5,
    max_social_accounts INTEGER DEFAULT 10,
    max_posts_per_day INTEGER DEFAULT 50,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS brands (
    id TEXT PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    logo_url TEXT,
    colors JSONB DEFAULT '{"primary": "#6366F1", "secondary": "#8B5CF6", "accent": "#06B6D4", "background": "#0F172A", "text": "#F8FAFC"}'::jsonb,
    tone TEXT DEFAULT 'professional',
    language TEXT DEFAULT 'en',
    audience_description TEXT,
    hashtags JSONB DEFAULT '["TechTips", "Coding", "SoftwareEngineering"]'::jsonb,
    topic_categories JSONB DEFAULT '[]'::jsonb,
    custom_topics JSONB DEFAULT '{}'::jsonb,
    posting_schedule JSONB DEFAULT '{"timezone": "UTC", "content_hours": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19], "max_posts_per_day": 10, "min_interval_minutes": 90}'::jsonb,
    ai_profile_id TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS ai_profiles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    expertise TEXT DEFAULT 'Software engineering, system design, and modern technology',
    tone TEXT DEFAULT 'professional',
    style TEXT DEFAULT 'clear and structured',
    cta_style TEXT DEFAULT 'question',
    emoji_level TEXT DEFAULT 'medium',
    complexity TEXT DEFAULT 'intermediate',
    writing_style TEXT DEFAULT 'structured with bullet points and clear sections',
    prompt_template TEXT,
    system_prompt_override TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS social_accounts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    brand_id TEXT REFERENCES brands(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    platform_account_id TEXT,
    account_name TEXT,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMP WITH TIME ZONE,
    app_secret TEXT,
    platform_config JSONB DEFAULT '{}'::jsonb,
    timezone TEXT DEFAULT 'UTC',
    posting_schedule JSONB DEFAULT '{"content_hours": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19], "max_posts_per_day": 10}'::jsonb,
    preferred_content_types JSONB DEFAULT '["image", "text"]'::jsonb,
    enabled BOOLEAN DEFAULT true,
    health_status TEXT DEFAULT 'unknown',
    last_health_check TIMESTAMP WITH TIME ZONE,
    last_publish_at TIMESTAMP WITH TIME ZONE,
    consecutive_failures INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS workspace_members (
    id TEXT PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    role TEXT DEFAULT 'viewer',
    invited_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    accepted_at TIMESTAMP WITH TIME ZONE
);

-- Note: RLS Policies should be applied if using Supabase Authentication, 
-- but omitted here to allow service role access from backend without 
-- complicated RLS setup for this internal API stage.
