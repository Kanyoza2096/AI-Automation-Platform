-- V70.2: atomic optimistic-concurrency transition for sales lead state.

CREATE OR REPLACE FUNCTION public.transition_v70_sales_lead(
    p_workspace_id uuid, p_lead_id text, p_expected_version bigint,
    p_state text, p_name text, p_email text, p_phone text, p_company text,
    p_amount numeric, p_event_id text, p_event_name text
) RETURNS TABLE(version bigint, state text) LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  RETURN QUERY
  UPDATE public.v70_sales_lead_state s
     SET state=p_state, name=coalesce(p_name,s.name), email=coalesce(p_email,s.email),
         phone=coalesce(p_phone,s.phone), company=coalesce(p_company,s.company),
         amount=coalesce(p_amount,s.amount), version=s.version+1,
         last_event_id=p_event_id, updated_by_event=p_event_name, updated_at=now()
   WHERE s.workspace_id=p_workspace_id AND s.lead_id=p_lead_id
     AND s.version=p_expected_version
  RETURNING s.version, s.state;
END;
$$;

REVOKE ALL ON FUNCTION public.transition_v70_sales_lead(uuid,text,bigint,text,text,text,text,text,numeric,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.transition_v70_sales_lead(uuid,text,bigint,text,text,text,text,text,numeric,text,text) TO service_role;
