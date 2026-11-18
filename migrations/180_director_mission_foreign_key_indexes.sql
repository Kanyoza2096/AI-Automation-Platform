-- Cover the child-side foreign keys on the Director mission aggregate.
-- The mission tables are initially empty, so ordinary transactional index
-- creation is safe in staging; use a separate concurrent-index rollout if the
-- production table grows materially before this migration is promoted.

CREATE INDEX IF NOT EXISTS director_missions_plan_id_idx
    ON public.director_missions (plan_id);

CREATE INDEX IF NOT EXISTS director_missions_workspace_admission_idx
    ON public.director_missions (workspace_id, admission_idempotency_key);
