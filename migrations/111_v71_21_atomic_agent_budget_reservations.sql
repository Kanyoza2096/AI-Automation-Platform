-- V71.21 — Atomic distributed agent-budget reservation ledger.
-- Admission/resource control only. It never grants execution authority.
ALTER TABLE agent_budgets
    ADD COLUMN IF NOT EXISTS reserved_actions BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reserved_cost NUMERIC NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reserved_risk_units NUMERIC NOT NULL DEFAULT 0;

ALTER TABLE agent_budgets
    DROP CONSTRAINT IF EXISTS agent_budgets_reserved_nonnegative_ck;
ALTER TABLE agent_budgets
    ADD CONSTRAINT agent_budgets_reserved_nonnegative_ck
    CHECK (reserved_actions >= 0 AND reserved_cost >= 0 AND reserved_risk_units >= 0);

CREATE TABLE IF NOT EXISTS agent_budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    budget_id TEXT NOT NULL REFERENCES agent_budgets(budget_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    actions BIGINT NOT NULL DEFAULT 0,
    cost NUMERIC NOT NULL DEFAULT 0,
    risk_units NUMERIC NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'reserved',
    claim_token TEXT NOT NULL,
    lease_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agent_budget_reservation_status_ck CHECK (status IN ('reserved','committed','released','uncertain')),
    CONSTRAINT agent_budget_reservation_nonnegative_ck CHECK (actions >= 0 AND cost >= 0 AND risk_units >= 0),
    UNIQUE(workspace_id, budget_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_budget_reservations_lease ON agent_budget_reservations(status, lease_until);

CREATE OR REPLACE FUNCTION reserve_agent_budget(
    p_workspace_id TEXT, p_budget_id TEXT, p_run_id TEXT, p_reservation_id TEXT,
    p_claim_token TEXT, p_lease_seconds INTEGER, p_actions BIGINT, p_cost NUMERIC, p_risk_units NUMERIC
) RETURNS SETOF agent_budget_reservations LANGUAGE plpgsql AS $$
DECLARE r agent_budget_reservations;
BEGIN
    IF p_actions < 0 OR p_cost < 0 OR p_risk_units < 0 THEN RAISE EXCEPTION 'negative_budget_delta'; END IF;
    SELECT * INTO r FROM agent_budget_reservations
      WHERE workspace_id=p_workspace_id AND budget_id=p_budget_id AND run_id=p_run_id FOR UPDATE;
    IF FOUND THEN
        IF r.status IN ('reserved','committed') THEN RETURN NEXT r; RETURN; END IF;
    END IF;

    UPDATE agent_budgets SET
      reserved_actions = reserved_actions + p_actions,
      reserved_cost = reserved_cost + p_cost,
      reserved_risk_units = reserved_risk_units + p_risk_units,
      updated_at = NOW()
    WHERE budget_id=p_budget_id AND workspace_id=p_workspace_id AND status='active'
      AND (max_actions IS NULL OR used_actions + reserved_actions + p_actions <= max_actions)
      AND (max_cost IS NULL OR used_cost + reserved_cost + p_cost <= max_cost)
      AND (max_risk_units IS NULL OR used_risk_units + reserved_risk_units + p_risk_units <= max_risk_units);
    IF NOT FOUND THEN RAISE EXCEPTION 'insufficient_or_inactive_budget'; END IF;

    INSERT INTO agent_budget_reservations(reservation_id,workspace_id,budget_id,run_id,actions,cost,risk_units,claim_token,lease_until)
    VALUES(p_reservation_id,p_workspace_id,p_budget_id,p_run_id,p_actions,p_cost,p_risk_units,p_claim_token,NOW()+make_interval(secs=>GREATEST(p_lease_seconds,1)))
    RETURNING * INTO r;
    RETURN NEXT r;
END $$;

CREATE OR REPLACE FUNCTION finalize_agent_budget_reservation(
    p_workspace_id TEXT, p_reservation_id TEXT, p_claim_token TEXT, p_commit BOOLEAN
) RETURNS SETOF agent_budget_reservations LANGUAGE plpgsql AS $$
DECLARE r agent_budget_reservations;
BEGIN
    SELECT * INTO r FROM agent_budget_reservations
      WHERE reservation_id=p_reservation_id AND workspace_id=p_workspace_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    IF r.status <> 'reserved' OR r.claim_token <> p_claim_token THEN RETURN NEXT r; RETURN; END IF;
    IF p_commit THEN
      UPDATE agent_budgets SET reserved_actions=reserved_actions-r.actions, reserved_cost=reserved_cost-r.cost,
        reserved_risk_units=reserved_risk_units-r.risk_units,
        used_actions=used_actions+r.actions, used_cost=used_cost+r.cost, used_risk_units=used_risk_units+r.risk_units,
        status=CASE WHEN (max_actions IS NOT NULL AND used_actions+r.actions>=max_actions)
          OR (max_cost IS NOT NULL AND used_cost+r.cost>=max_cost)
          OR (max_risk_units IS NOT NULL AND used_risk_units+r.risk_units>=max_risk_units) THEN 'exhausted' ELSE status END,
        updated_at=NOW() WHERE budget_id=r.budget_id AND workspace_id=p_workspace_id;
      UPDATE agent_budget_reservations SET status='committed', updated_at=NOW() WHERE reservation_id=r.reservation_id;
    ELSE
      UPDATE agent_budgets SET reserved_actions=reserved_actions-r.actions, reserved_cost=reserved_cost-r.cost,
        reserved_risk_units=reserved_risk_units-r.risk_units, updated_at=NOW()
        WHERE budget_id=r.budget_id AND workspace_id=p_workspace_id;
      UPDATE agent_budget_reservations SET status='released', updated_at=NOW() WHERE reservation_id=r.reservation_id;
    END IF;
    SELECT * INTO r FROM agent_budget_reservations WHERE reservation_id=p_reservation_id;
    RETURN NEXT r;
END $$;

CREATE OR REPLACE FUNCTION recover_agent_budget_reservations(p_limit INTEGER DEFAULT 100)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE n INTEGER;
BEGIN
  WITH expired AS (
    SELECT reservation_id FROM agent_budget_reservations WHERE status='reserved' AND lease_until<NOW() FOR UPDATE SKIP LOCKED LIMIT GREATEST(p_limit,1)
  )
  UPDATE agent_budget_reservations r SET status='uncertain', updated_at=NOW()
    FROM expired e WHERE r.reservation_id=e.reservation_id;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
