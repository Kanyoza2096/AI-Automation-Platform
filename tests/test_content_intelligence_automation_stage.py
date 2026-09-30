import asyncio
from services.business_automation import BusinessAutomationEngine
from services.content_intelligence import ContentIntelligenceEngine

def test_condition_evaluator():
    e = BusinessAutomationEngine()
    assert e.evaluate({"field":"score","op":"gte","value":10},{"score":12})
    assert not e.evaluate({"field":"score","op":"lt","value":10},{"score":12})
    assert e.evaluate({"all":[{"field":"ok","op":"truthy"},{"field":"kind","op":"eq","value":"lead"}]},{"ok":True,"kind":"lead"})

def test_automation_rule_workspace_isolation_and_dry_run():
    async def run():
        e=BusinessAutomationEngine()
        r=await e.create_rule("ws1",{"name":"test","condition":{"field":"kind","op":"eq","value":"lead"},"action":"log"})
        assert len(await e.list_rules("ws2"))==0
        out=await e.run("ws1",event={"kind":"lead"},dry_run=True)
        assert out and out[0].status=="dry_run"
    asyncio.run(run())

def test_intelligence_degrades_without_evidence():
    class EmptyWeb:
        async def search_async(self,*a,**k): return []
        async def fetch_async(self,*a,**k): return None
    async def run():
        r=await ContentIntelligenceEngine(web_gateway=EmptyWeb()).analyze("test")
        assert r.mode=="unavailable" and r.confidence==0.0
    asyncio.run(run())

def test_intelligence_synthesizes_from_evidence():
    class Web:
        async def search_async(self,*a,**k):
            class R: pass
            r=R(); r.title="Authoritative note"; r.url="https://example.com/a"; r.snippet="Fact A"; r.source="test"; r.score=1.0
            return [r]
        async def fetch_async(self,*a,**k):
            class P: text="Fact A full text"
            return P()
    class Resp:
        ok=True
        text='''{"summary":"Fact A","angles":["Angle A"],"claims":["Fact A"],"risks":[],"recommendations":["Use evidence"],"confidence":0.9}'''
        mode=type("M",(),{"value":"full"})()
    class LLM:
        async def complete(self,*a,**k): return Resp()
    async def run():
        r=await ContentIntelligenceEngine(web_gateway=Web(),llm_gateway=LLM()).analyze("topic")
        assert r.mode=="full" and r.confidence==0.9 and r.claims==["Fact A"]
        assert r.evidence[0]["snippet"]=="Fact A"
    asyncio.run(run())

def test_business_automation_condition_decision_delegates_to_condition_engine():
    class FakePlan:
        id = "plan_test"
    class FakeEngine:
        def observe(self, observation):
            self.observation = observation
            return {"decision": {"id": "decision_test"}}
        def get_decision(self, decision_id):
            return {"id": decision_id}
        def create_action_plan(self, decision):
            self.decision = decision
            return FakePlan()
    async def run():
        import services.condition_engine as ce
        original = ce.autonomous_engine
        fake = FakeEngine()
        ce.autonomous_engine = fake
        try:
            engine = BusinessAutomationEngine()
            result = await engine._action_condition_decision(
                {"workspace_id": "ws1", "brand_id": "brand1", "post_id": "post1", "likes": 10, "impressions": 100},
                {},
            )
            assert result["ok"] is True
            assert result["plan_id"] == "plan_test"
            assert fake.observation.metadata["workspace_id"] == "ws1"
        finally:
            ce.autonomous_engine = original
    asyncio.run(run())


def test_business_automation_registers_governed_internal_adapters():
    e = BusinessAutomationEngine()
    expected = {
        "crm_add_contact", "crm_add_deal", "crm_schedule_followup",
        "crm_add_meeting", "generate_workspace_report", "notify_operator",
    }
    assert expected.issubset(set(e._actions))
    assert "shell" not in e._actions and "http_request" not in e._actions


def test_crm_repository_fails_closed_on_durable_write_error():
    class FailingDB:
        def table(self, name):
            return self
        def insert(self, payload):
            return self
        def execute(self):
            raise RuntimeError("database down")

    from plugins.industries.crm.repository import CRMRepository
    repo = CRMRepository(FailingDB())
    try:
        repo.add_contact("ws1", {"contact_name": "Jane"})
        assert False, "CRM write must fail when authoritative persistence fails"
    except RuntimeError as exc:
        assert "persistence failed" in str(exc)


def test_business_automation_does_not_mark_failed_crm_result_completed(monkeypatch):
    import services.business_automation as ba

    class FakePlugin:
        def execute(self, *args, **kwargs):
            return {"ok": False, "error": "authoritative CRM failure"}

    monkeypatch.setattr(ba.BusinessAutomationEngine, "_crm_plugin", staticmethod(lambda ws: (FakePlugin(), ws)))
    async def run():
        e = ba.BusinessAutomationEngine()
        r = await e._action_crm_add_contact({"name": "Jane"}, {}, workspace_id="ws1")
        return r
    try:
        asyncio.run(run())
        assert False, "failed domain result must propagate as an exception"
    except RuntimeError as exc:
        assert "authoritative CRM failure" in str(exc)
