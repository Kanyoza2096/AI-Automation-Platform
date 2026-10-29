CREATE TABLE IF NOT EXISTS kanyoza_notifications (
  id text PRIMARY KEY,
  workspace_id text NOT NULL,
  recipient_id text NOT NULL,
  channel text NOT NULL,
  subject text,
  body text NOT NULL,
  priority text NOT NULL DEFAULT 'normal',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  delivered boolean NOT NULL DEFAULT false,
  delivered_at timestamptz,
  error text,
  correlation_id text,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kanyoza_notifications_workspace_created
  ON kanyoza_notifications(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kanyoza_notifications_workspace_unread
  ON kanyoza_notifications(workspace_id, read_at, created_at DESC);

ALTER TABLE kanyoza_notifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kanyoza_notifications_workspace_select ON kanyoza_notifications;
CREATE POLICY kanyoza_notifications_workspace_select
  ON kanyoza_notifications FOR SELECT TO authenticated
  USING ((select auth.uid())::text = recipient_id OR (select auth.jwt() ->> 'workspace_id') = workspace_id);

DROP POLICY IF EXISTS kanyoza_notifications_workspace_update ON kanyoza_notifications;
CREATE POLICY kanyoza_notifications_workspace_update
  ON kanyoza_notifications FOR UPDATE TO authenticated
  USING ((select auth.uid())::text = recipient_id OR (select auth.jwt() ->> 'workspace_id') = workspace_id)
  WITH CHECK ((select auth.uid())::text = recipient_id OR (select auth.jwt() ->> 'workspace_id') = workspace_id);
