-- V71.21 follow-up: uncertain budget reservations must not strand reserved capacity.
-- An expired reservation is conservatively treated as consumed capacity until
-- explicit business reconciliation says otherwise. Therefore recovery moves the
-- reservation from reserved -> uncertain AND transfers its reservation amounts
-- into used amounts atomically.

CREATE OR REPLACE FUNCTION recover_agent_budget_reservations(p_limit INTEGER DEFAULT 100)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE n INTEGER;
BEGIN
  WITH expired AS (
    SELECT reservation_id
      FROM agent_budget_reservations
     WHERE status='reserved' AND lease_until<NOW()
     ORDER BY lease_until
     FOR UPDATE SKIP LOCKED
     LIMIT GREATEST(p_limit,1)
  ),
  moved AS (
    UPDATE agent_budget_reservations r
       SET status='uncertain', updated_at=NOW()
      FROM expired e
     WHERE r.reservation_id=e.reservation_id
     RETURNING r.*
  )
  UPDATE agent_budgets b
     SET reserved_actions=b.reserved_actions-m.actions,
         reserved_cost=b.reserved_cost-m.cost,
         reserved_risk_units=b.reserved_risk_units-m.risk_units,
         used_actions=b.used_actions+m.actions,
         used_cost=b.used_cost+m.cost,
         used_risk_units=b.used_risk_units+m.risk_units,
         status=CASE
           WHEN (b.max_actions IS NOT NULL AND b.used_actions+m.actions>=b.max_actions)
             OR (b.max_cost IS NOT NULL AND b.used_cost+m.cost>=b.max_cost)
             OR (b.max_risk_units IS NOT NULL AND b.used_risk_units+m.risk_units>=b.max_risk_units)
           THEN 'exhausted' ELSE b.status END,
         updated_at=NOW()
    FROM moved m
   WHERE b.budget_id=m.budget_id
     AND b.workspace_id=m.workspace_id;

  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
