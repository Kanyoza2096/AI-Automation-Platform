-- Phase 16.16: bind queued builds to a verified Council / Action Kernel admission receipt.
-- Existing rows remain unclaimable until explicitly admitted through the governed path.
ALTER TABLE public.kdo_engineering_build_jobs
  ADD COLUMN IF NOT EXISTS admission_decision_id TEXT,
  ADD COLUMN IF NOT EXISTS admission_intent_id TEXT,
  ADD COLUMN IF NOT EXISTS admission_execution_id TEXT,
  ADD COLUMN IF NOT EXISTS admission_receipt_verified BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS admitted_at TIMESTAMPTZ;

ALTER TABLE public.kdo_engineering_build_jobs
  ADD CONSTRAINT kdo_build_jobs_verified_receipt_consistency
  CHECK (
    (admission_receipt_verified = FALSE AND admission_decision_id IS NULL
      AND admission_intent_id IS NULL AND admission_execution_id IS NULL AND admitted_at IS NULL)
    OR
    (admission_receipt_verified = TRUE AND admission_decision_id IS NOT NULL
      AND admission_intent_id IS NOT NULL AND admission_execution_id IS NOT NULL AND admitted_at IS NOT NULL)
  );

CREATE INDEX IF NOT EXISTS idx_kdo_build_jobs_verified_ready
  ON public.kdo_engineering_build_jobs(created_at, job_id)
  WHERE status = 'queued' AND admission_receipt_verified = TRUE;

COMMENT ON COLUMN public.kdo_engineering_build_jobs.admission_receipt_verified IS
  'True only after the configured authoritative Action Kernel receipt verifier confirms the Council-approved enqueue execution.';
