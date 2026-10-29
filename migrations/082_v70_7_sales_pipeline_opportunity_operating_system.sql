-- V70.7 durable projection for sales opportunity operating state.
CREATE TABLE IF NOT EXISTS sales_opportunity_operating_state (
    workspace_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL,
    contact_id TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    amount NUMERIC NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL,
    probability INTEGER NOT NULL DEFAULT 20,
    expected_close_date TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    last_event_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, opportunity_id)
);
CREATE INDEX IF NOT EXISTS idx_sales_opp_pipeline_stage ON sales_opportunity_operating_state(workspace_id, stage);
CREATE INDEX IF NOT EXISTS idx_sales_opp_close_date ON sales_opportunity_operating_state(workspace_id, expected_close_date);
