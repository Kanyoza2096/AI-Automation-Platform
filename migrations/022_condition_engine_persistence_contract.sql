-- 022: align Condition Engine persistence with the deployed JSONB tables.
-- Additive only: preserve existing bigint identity keys and all stored rows.
-- The generated keys provide idempotent upserts for the JSONB payload contracts.

ALTER TABLE public.autonomous_decisions
    ADD COLUMN IF NOT EXISTS decision_key TEXT
    GENERATED ALWAYS AS (decision ->> 'id') STORED;

CREATE UNIQUE INDEX IF NOT EXISTS autonomous_decisions_decision_key_uidx
    ON public.autonomous_decisions (decision_key)
    WHERE decision_key IS NOT NULL;

ALTER TABLE public.autonomous_decision_memory
    ADD COLUMN IF NOT EXISTS memory_key TEXT
    GENERATED ALWAYS AS (value ->> 'decision_id') STORED;

CREATE UNIQUE INDEX IF NOT EXISTS autonomous_decision_memory_memory_key_uidx
    ON public.autonomous_decision_memory (memory_key)
    WHERE memory_key IS NOT NULL;
