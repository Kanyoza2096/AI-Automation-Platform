-- C7.3: durable scheduled-post claim + recovery fencing.
-- A scheduler may only claim a queued post once. Expired claims are recoverable
-- only when no publish outbox has been linked; linked work remains outbox-owned.

ALTER TABLE IF EXISTS public.scheduled_posts
    ADD COLUMN IF NOT EXISTS lease_token TEXT,
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheduled_posts_workspace_idempotency
    ON public.scheduled_posts(workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_scheduled_posts_claim_recovery
    ON public.scheduled_posts(status, lease_expires_at)
    WHERE status = 'processing';

CREATE OR REPLACE FUNCTION public.claim_scheduled_post(
    p_id TEXT, p_worker TEXT, p_lease_seconds INTEGER DEFAULT 300
) RETURNS SETOF public.scheduled_posts
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE
    v_token TEXT := encode(gen_random_bytes(16), 'hex');
BEGIN
    RETURN QUERY
    UPDATE public.scheduled_posts
       SET status = 'processing',
           started_at = COALESCE(started_at, now()),
           lease_token = v_token,
           lease_owner = p_worker,
           lease_expires_at = now() + make_interval(secs => GREATEST(p_lease_seconds, 1)),
           attempts = COALESCE(attempts, 0) + 1
     WHERE id = p_id
       AND status = 'queued'
       AND (scheduled_for IS NULL OR scheduled_for <= now())
    RETURNING *;
END;
$$;

CREATE OR REPLACE FUNCTION public.recover_scheduled_post_claims(
    p_limit INTEGER DEFAULT 100
) RETURNS TABLE(id TEXT, new_status TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT s.id
          FROM public.scheduled_posts s
         WHERE s.status = 'processing'
           AND s.lease_expires_at IS NOT NULL
           AND s.lease_expires_at < now()
         ORDER BY s.lease_expires_at
         LIMIT GREATEST(p_limit, 1)
         FOR UPDATE SKIP LOCKED
    )
    UPDATE public.scheduled_posts s
       SET status = CASE
               WHEN s.outbox_id IS NOT NULL THEN 'outbox_pending'
               ELSE 'queued'
           END,
           started_at = CASE WHEN s.outbox_id IS NOT NULL THEN s.started_at ELSE NULL END,
           lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL
      FROM candidates c
     WHERE s.id = c.id
    RETURNING s.id, s.status;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_scheduled_post(TEXT, TEXT, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.recover_scheduled_post_claims(INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_scheduled_post(TEXT, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.recover_scheduled_post_claims(INTEGER) TO service_role;
