-- V68.14: tenant-bound outcome integrity.
-- Outcome evidence must never connect an execution from another workspace.

CREATE OR REPLACE FUNCTION public.validate_business_outcome_observation_execution_tenant()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE execution_ws uuid;
BEGIN
  SELECT workspace_id INTO execution_ws
    FROM public.external_execution_ledger
   WHERE execution_id = NEW.execution_id;

  IF execution_ws IS NULL THEN
    RAISE EXCEPTION 'business outcome observation references unknown execution %', NEW.execution_id;
  END IF;

  IF execution_ws <> NEW.workspace_id THEN
    RAISE EXCEPTION 'business outcome observation execution workspace mismatch';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_business_outcome_observation_execution_tenant
  ON public.business_outcome_observations;
CREATE TRIGGER trg_validate_business_outcome_observation_execution_tenant
BEFORE INSERT OR UPDATE OF execution_id, workspace_id
ON public.business_outcome_observations
FOR EACH ROW EXECUTE FUNCTION public.validate_business_outcome_observation_execution_tenant();

CREATE OR REPLACE FUNCTION public.validate_business_outcome_attribution_tenant()
RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE observation_ws uuid; execution_ws uuid;
BEGIN
  SELECT workspace_id INTO observation_ws
    FROM public.business_outcome_observations
   WHERE observation_id = NEW.observation_id;
  IF observation_ws IS NULL THEN
    RAISE EXCEPTION 'business attribution references unknown observation %', NEW.observation_id;
  END IF;
  IF observation_ws <> NEW.workspace_id THEN
    RAISE EXCEPTION 'business attribution observation workspace mismatch';
  END IF;

  IF NEW.execution_id IS NOT NULL THEN
    SELECT workspace_id INTO execution_ws
      FROM public.external_execution_ledger
     WHERE execution_id = NEW.execution_id;
    IF execution_ws IS NULL THEN
      RAISE EXCEPTION 'business attribution references unknown execution %', NEW.execution_id;
    END IF;
    IF execution_ws <> NEW.workspace_id THEN
      RAISE EXCEPTION 'business attribution execution workspace mismatch';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_business_outcome_attribution_tenant
  ON public.business_outcome_attributions;
CREATE TRIGGER trg_validate_business_outcome_attribution_tenant
BEFORE INSERT OR UPDATE OF observation_id, execution_id, workspace_id
ON public.business_outcome_attributions
FOR EACH ROW EXECUTE FUNCTION public.validate_business_outcome_attribution_tenant();
