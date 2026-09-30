from services.business_automation_catalog import get_template, list_templates


def test_sales_catalog_contains_operational_event_templates():
    ids = {x["id"] for x in list_templates()}
    assert {"sales.new-lead-capture", "sales.new-lead-followup", "sales.qualified-lead-deal", "sales.follow-up-due"} <= ids


def test_sales_templates_have_business_outcomes_and_requirements():
    for template_id in ("sales.new-lead-capture", "sales.qualified-lead-deal", "sales.follow-up-due"):
        template = get_template(template_id)
        assert template
        assert template["outcome"]
        assert template["trigger"]
        assert template["requires"]


def test_website_lead_provider_is_fixed_to_lead_created():
    import asyncio
    import services.business_automation_event_sources as sources

    class Result:
        data = [{"source_id": "src1", "workspace_id": "ws1", "name": "site", "provider": "website_lead", "event_types": ["lead.created"], "active": True}]

    class Table:
        def insert(self, row):
            self.row = row
            return self
        def execute(self):
            return Result()

    class DB:
        def table(self, _): return Table()

    original = sources._db
    sources._db = lambda: DB()
    try:
        public, secret = asyncio.run(sources.create_source("ws1", "site", "website_lead", ["invoice.overdue"]))
        assert public["event_types"] == ["lead.created"]
        assert secret.startswith("kdo_wh_")
    finally:
        sources._db = original
