-- 023: normalize generated-key indexes for PostgREST ON CONFLICT targets.
-- Unique indexes permit multiple NULL values, so no partial predicate is needed.
DROP INDEX IF EXISTS public.autonomous_decisions_decision_key_uidx;
CREATE UNIQUE INDEX autonomous_decisions_decision_key_uidx
    ON public.autonomous_decisions (decision_key);

DROP INDEX IF EXISTS public.autonomous_decision_memory_memory_key_uidx;
CREATE UNIQUE INDEX autonomous_decision_memory_memory_key_uidx
    ON public.autonomous_decision_memory (memory_key);
