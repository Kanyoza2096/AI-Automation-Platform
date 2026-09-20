import asyncio
from services.business_automation import BusinessAutomationEngine
from services.business_automation_event_bridge import BusinessAutomationEventBridge


def test_durable_delivery_completion_requires_claim_fence():
    class Result:
        def __init__(self, data): self.data = data
    class RPC:
        def __init__(self, db, name, args): self.db, self.name, self.args = db, name, args
        def execute(self): self.db.calls.append((self.name, self.args)); return Result(self.db.responses.get(self.name, True))
    class DB:
        def __init__(self): self.calls=[]; self.responses={"complete_business_automation_event_delivery": True}
        def rpc(self,n,a): return RPC(self,n,a)
    async def run():
        db=DB(); bridge=BusinessAutomationEventBridge(BusinessAutomationEngine(db))
        await bridge._ack_durable({"delivery_id":"d1","claim_token":"worker-a"}, success=True)
        name,args=db.calls[-1]
        assert name == "complete_business_automation_event_delivery"
        assert args["p_claim_token"] == "worker-a"
    asyncio.run(run())


def test_durable_delivery_without_claim_token_fails_closed():
    async def run():
        bridge=BusinessAutomationEventBridge(BusinessAutomationEngine())
        try:
            await bridge._ack_durable({"delivery_id":"d1"}, success=True)
        except RuntimeError as exc:
            assert "claim token" in str(exc)
        else:
            raise AssertionError("completion without ownership token must fail closed")
    asyncio.run(run())
