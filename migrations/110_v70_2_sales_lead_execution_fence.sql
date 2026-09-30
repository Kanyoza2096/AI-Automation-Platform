-- C10.2: per-lead execution fence. Different events for the same lead may not
-- cross the consequential business boundary concurrently.
CREATE TABLE IF NOT EXISTS public.v70_sales_lead_execution_fence (
    workspace_id uuid NOT NULL,
    lead_id text NOT NULL,
    status text NOT NULL DEFAULT 'processing' CHECK (status = 'processing'),
    claim_token text NOT NULL,
    claim_owner text NOT NULL,
    lease_until timestamptz NOT NULL,
    attempts integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, lead_id)
);

CREATE INDEX IF NOT EXISTS idx_v70_sales_lead_execution_recovery
ON public.v70_sales_lead_execution_fence(workspace_id, lease_until);

CREATE OR REPLACE FUNCTION public.claim_v70_sales_lead_execution(
    p_workspace_id uuid, p_lead_id text, p_worker text, p_lease_seconds integer DEFAULT 300
) RETURNS TABLE(claim_token text, status text, attempts integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_token text := encode(gen_random_bytes(16), 'hex');
BEGIN
  INSERT INTO public.v70_sales_lead_execution_fence
    (workspace_id, lead_id, status, claim_token, claim_owner, lease_until, attempts)
  VALUES
    (p_workspace_id, p_lead_id, 'processing', v_token, p_worker,
     now()+make_interval(secs=>greatest(p_lease_seconds,1)), 1)
  ON CONFLICT (workspace_id, lead_id) DO UPDATE
    SET claim_token=v_token, claim_owner=p_worker,
        lease_until=now()+make_interval(secs=>greatest(p_lease_seconds,1)),
        attempts=public.v70_sales_lead_execution_fence.attempts+1, updated_at=now()
  WHERE public.v70_sales_lead_execution_fence.lease_until < now();
  RETURN QUERY SELECT e.claim_token, e.status, e.attempts
    FROM public.v70_sales_lead_execution_fence e
   WHERE e.workspace_id=p_workspace_id AND e.lead_id=p_lead_id
     AND e.claim_owner=p_worker AND e.claim_token=v_token;
END; $$;

CREATE OR REPLACE FUNCTION public.release_v70_sales_lead_execution(
    p_workspace_id uuid, p_lead_id text, p_claim_token text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  DELETE FROM public.v70_sales_lead_execution_fence
   WHERE workspace_id=p_workspace_id AND lead_id=p_lead_id
     AND claim_token=p_claim_token;
  RETURN FOUND;
END; $$;

REVOKE ALL ON FUNCTION public.claim_v70_sales_lead_execution(uuid,text,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_v70_sales_lead_execution(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_v70_sales_lead_execution(uuid,text,text,integer) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_v70_sales_lead_execution(uuid,text,text) TO service_role;
