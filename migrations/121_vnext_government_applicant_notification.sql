CREATE TABLE IF NOT EXISTS government_applicant_notifications (
  id text PRIMARY KEY,
  workspace_id text NOT NULL,
  permit_case_id text NOT NULL,
  applicant_ref text NOT NULL,
  to_number text NOT NULL,
  body text NOT NULL,
  idempotency_key text NOT NULL,
  provider_message_sid text,
  provider_status text NOT NULL DEFAULT 'pending',
  status text NOT NULL DEFAULT 'pending',
  reconciliation_required boolean NOT NULL DEFAULT true,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_gov_applicant_notifications_reconcile ON government_applicant_notifications(workspace_id, reconciliation_required);
CREATE TABLE IF NOT EXISTS government_applicant_notification_reconciliation_evidence (
  id bigserial PRIMARY KEY,
  workspace_id text NOT NULL,
  notification_id text NOT NULL,
  provider text NOT NULL,
  provider_reference text,
  attempt_number integer NOT NULL,
  provider_status text,
  canonical_status text NOT NULL,
  terminal boolean NOT NULL,
  verification_state text NOT NULL,
  observed jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gov_applicant_notification_evidence ON government_applicant_notification_reconciliation_evidence(workspace_id, notification_id, recorded_at DESC);
