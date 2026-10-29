-- Phase 4: durable Healthcare patient-message delivery and provider reconciliation evidence.
CREATE TABLE IF NOT EXISTS public.healthcare_patient_message_deliveries (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, patient_id TEXT NOT NULL,
    to_number TEXT NOT NULL, body TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'twilio', provider_message_sid TEXT,
    provider_status TEXT NOT NULL DEFAULT 'unknown', status TEXT NOT NULL DEFAULT 'pending',
    reconciliation_required BOOLEAN NOT NULL DEFAULT TRUE, evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_healthcare_patient_message_pending ON public.healthcare_patient_message_deliveries(workspace_id, reconciliation_required, created_at);
CREATE TABLE IF NOT EXISTS public.healthcare_patient_message_reconciliation_evidence (
    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, delivery_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'twilio', provider_reference TEXT NOT NULL DEFAULT '',
    attempt_number INTEGER NOT NULL, provider_status TEXT NOT NULL, canonical_status TEXT NOT NULL,
    terminal BOOLEAN NOT NULL DEFAULT FALSE, verification_state TEXT NOT NULL,
    observed JSONB NOT NULL DEFAULT '{}'::jsonb, evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_healthcare_patient_message_recon_evidence ON public.healthcare_patient_message_reconciliation_evidence(workspace_id, delivery_id, attempt_number DESC);
