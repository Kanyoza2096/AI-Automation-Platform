-- V72.4 Business Cockpit truth query indexes. Read-model only; no authority.
CREATE INDEX IF NOT EXISTS idx_boo_workspace_state_updated ON business_operating_objectives(workspace_id, state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_opp_workspace_updated ON sales_opportunity_operating_state(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_internal_work_workspace_updated ON internal_business_work_state(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_obs_workspace_observed ON business_outcome_observations(workspace_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_outcome_attr_workspace_created ON business_outcome_attributions(workspace_id, created_at DESC);
