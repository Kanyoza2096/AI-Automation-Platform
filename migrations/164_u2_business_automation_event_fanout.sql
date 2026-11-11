-- U2: atomically provision the durable Business Automation inbox.
--
-- The bridge's claim RPC already consumes event_deliveries. Previously there
-- was no producer for consumer_group='business_automation', so the durable
-- inbox could be enabled while remaining permanently empty.
--
-- Only events matching an enabled rule in the same workspace are fanned out.
-- This migration intentionally does not backfill historical events: replaying
-- old events could activate automations that were not enabled when emitted.

CREATE OR REPLACE FUNCTION public.fanout_business_automation_event_delivery()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.workspace_id IS NULL OR NEW.event_type IS NULL THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.business_automation_rules AS rule
        WHERE rule.workspace_id = NEW.workspace_id
          AND rule.enabled IS TRUE
          AND (
              (
                  pg_catalog.jsonb_typeof(rule.parameters -> 'trigger_events') = 'array'
                  AND (
                      (rule.parameters -> 'trigger_events') ? NEW.event_type
                      OR (rule.parameters -> 'trigger_events') ? '*'
                  )
              )
              OR (
                  pg_catalog.jsonb_typeof(rule.parameters -> 'trigger_events') = 'string'
                  AND (rule.parameters ->> 'trigger_events') IN (NEW.event_type, '*')
              )
          )
    ) THEN
        INSERT INTO public.event_deliveries (
            event_id,
            consumer_group,
            event_type,
            processed
        )
        VALUES (
            NEW.id,
            'business_automation',
            NEW.event_type,
            FALSE
        )
        ON CONFLICT (event_id, consumer_group) DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.fanout_business_automation_event_delivery() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_fanout_business_automation_event_delivery ON public.events;
CREATE TRIGGER trg_fanout_business_automation_event_delivery
AFTER INSERT ON public.events
FOR EACH ROW
EXECUTE FUNCTION public.fanout_business_automation_event_delivery();

-- The runtime checks this RPC before advertising the durable inbox as ready.
-- A missing/disabled trigger therefore degrades readiness instead of silently
-- polling an empty inbox.
CREATE OR REPLACE FUNCTION public.business_automation_event_fanout_ready()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger AS trigger_row
        JOIN pg_catalog.pg_class AS relation
          ON relation.oid = trigger_row.tgrelid
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'events'
          AND trigger_row.tgname = 'trg_fanout_business_automation_event_delivery'
          AND trigger_row.tgfoid = 'public.fanout_business_automation_event_delivery()'::regprocedure
          AND trigger_row.tgisinternal IS FALSE
          AND trigger_row.tgenabled IN ('O', 'A')
    );
$$;

REVOKE ALL ON FUNCTION public.business_automation_event_fanout_ready() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.business_automation_event_fanout_ready() TO service_role;
