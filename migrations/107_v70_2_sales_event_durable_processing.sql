-- V70.2: durable event claim/finalization primitives.
-- Execution authority remains outside this table; this is an identity/lease fence.

ALTER TABLE IF EXISTS public.v70_sales_lead_event_fence
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'received',
    ADD COLUMN IF NOT EXISTS claim_token TEXT,
    ADD COLUMN IF NOT EXISTS claim_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_error TEXT,
    ADD COLUMN IF NOT EXISTS result_json JSONB,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$ BEGIN
  ALTER TABLE public.v70_sales_lead_event_fence
    ADD CONSTRAINT v70_sales_event_status_ck CHECK (status IN ('received','processing','completed','failed','uncertain','retryable'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_v70_sales_event_claim_recovery
  ON public.v70_sales_lead_event_fence(workspace_id, status, lease_until);

CREATE OR REPLACE FUNCTION public.claim_v70_sales_event(
    p_workspace_id uuid, p_event_id text, p_worker text, p_lease_seconds integer DEFAULT 300
) RETURNS TABLE(claim_token text, status text, attempts integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE v_token text := encode(gen_random_bytes(16), 'hex');
BEGIN
  RETURN QUERY
  UPDATE public.v70_sales_lead_event_fence e
     SET status='processing', claim_token=v_token, claim_owner=p_worker,
         lease_until=now()+make_interval(secs=>greatest(p_lease_seconds,1)),
         attempts=coalesce(e.attempts,0)+1, updated_at=now()
   WHERE e.workspace_id=p_workspace_id AND e.event_id=p_event_id
     AND (e.status IN ('received','retryable') OR (e.status='processing' AND e.lease_until < now()))
   RETURNING e.claim_token, e.status, e.attempts;
END;
$$;

CREATE OR REPLACE FUNCTION public.renew_v70_sales_event_lease(
    p_workspace_id uuid, p_event_id text, p_claim_token text, p_lease_seconds integer DEFAULT 300
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE public.v70_sales_lead_event_fence
     SET lease_until=now()+make_interval(secs=>greatest(p_lease_seconds,1)), updated_at=now()
   WHERE workspace_id=p_workspace_id AND event_id=p_event_id
     AND status='processing' AND claim_token=p_claim_token;
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.finalize_v70_sales_event(
    p_workspace_id uuid, p_event_id text, p_claim_token text, p_status text,
    p_result_json jsonb DEFAULT NULL, p_error text DEFAULT NULL
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_status NOT IN ('completed','failed','uncertain','retryable') THEN
    RAISE EXCEPTION 'invalid terminal event status';
  END IF;
  UPDATE public.v70_sales_lead_event_fence
     SET status=p_status, result_json=p_result_json, last_error=p_error,
         completed_at=CASE WHEN p_status IN ('completed','failed') THEN now() ELSE NULL END,
         lease_until=NULL, claim_token=NULL, claim_owner=NULL, updated_at=now()
   WHERE workspace_id=p_workspace_id AND event_id=p_event_id
     AND status='processing' AND claim_token=p_claim_token;
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.mark_expired_v70_sales_events_uncertain(
    p_workspace_id uuid, p_limit integer DEFAULT 100
) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_count integer;
BEGIN
  WITH candidates AS (
    SELECT workspace_id,event_id FROM public.v70_sales_lead_event_fence
     WHERE workspace_id=p_workspace_id AND status='processing'
       AND lease_until IS NOT NULL AND lease_until < now()
     ORDER BY lease_until LIMIT greatest(p_limit,1) FOR UPDATE SKIP LOCKED
  )
  UPDATE public.v70_sales_lead_event_fence e
     SET status='uncertain', last_error='processing lease expired',
         lease_until=NULL, claim_token=NULL, claim_owner=NULL, updated_at=now()
    FROM candidates c WHERE e.workspace_id=c.workspace_id AND e.event_id=c.event_id;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_v70_sales_event(uuid,text,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.renew_v70_sales_event_lease(uuid,text,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.finalize_v70_sales_event(uuid,text,text,text,jsonb,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.mark_expired_v70_sales_events_uncertain(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_v70_sales_event(uuid,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.renew_v70_sales_event_lease(uuid,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_v70_sales_event(uuid,text,text,text,jsonb,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.mark_expired_v70_sales_events_uncertain(uuid,integer) TO service_role;
