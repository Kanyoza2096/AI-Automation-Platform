import pytest
from services.ipc15_objective_replanning import recommend_replan
from services.ipc16_autonomous_objective_execution import admit_next_objective_cycle, validate_cycle_lineage

class Workforce:
    def __init__(self): self.created = []
    def create(self, **kwargs):
        self.created.append(kwargs)
        return type("Run", (), {"run_id": kwargs["run_id"]})()

def verification(state="objective_progress"):
    return {"verification_id":"ov1","workspace_id":"ws1","objective_id":"obj1","state":state,"authority":"none"}

def objective(): return {"workspace_id":"ws1","objective_id":"obj1","state":"active","version":3}

def source_run():
    return {"run_id":"run-old","workspace_id":"ws1","objective_id":"obj1","agent_id":"agent1","plan_id":"plan1","objective":"grow leads","workflow_id":"lead_followup","event":{},"budget_id":None,"coordination_id":None}

def context():
    return {"schema_version":"1.0","workspace_id":"ws1","objective_id":"obj1","objective":"grow leads","state":"active","business_context":{},"success_criteria":{},"authority":"none","context_fingerprint":"x"}

def rec(state="objective_progress"):
    return recommend_replan(verification=verification(state), objective=objective())

def test_governed_progress_creates_next_durable_intent():
    w=Workforce(); r=rec(); a=admit_next_objective_cycle(recommendation=r,objective=objective(),governed_decision={"decision_id":"d1","workspace_id":"ws1","objective_id":"obj1","authority":"policy","decision":"allow"},source_run=source_run(),workforce=w,objective_context=context())
    assert a.created and w.created[0]["trigger"] == "objective_replan:replan_" + r["replan_id"].split("replan_",1)[1]

def test_cross_workspace_rejected():
    with pytest.raises(ValueError, match="workspace"):
        admit_next_objective_cycle(recommendation=rec(),objective={**objective(),"workspace_id":"ws2"},governed_decision={"decision_id":"d1","workspace_id":"ws2","objective_id":"obj1","authority":"policy","decision":"allow"},source_run=source_run(),workforce=Workforce(),objective_context=context())

def test_no_governed_decision_means_no_cycle():
    with pytest.raises(ValueError, match="governed cycle decision"):
        admit_next_objective_cycle(recommendation=rec(),objective=objective(),governed_decision={"authority":"none","decision":"allow"},source_run=source_run(),workforce=Workforce(),objective_context=context())

def test_adaptation_preserves_lineage():
    w=Workforce(); r=rec("objective_regression"); a=admit_next_objective_cycle(recommendation=r,objective=objective(),governed_decision={"decision_id":"d2","workspace_id":"ws1","objective_id":"obj1","authority":"policy","decision":"allow"},source_run=source_run(),workforce=w,objective_context=context())
    run={**source_run(),"run_id":a.run_id,"event":w.created[0]["event"]}
    validate_cycle_lineage(run=run,recommendation=r,workspace_id="ws1",objective_id="obj1")

def test_tampered_replan_is_rejected():
    w=Workforce(); r=rec(); r["disposition"]="adapt"
    with pytest.raises(ValueError, match="fingerprint"):
        admit_next_objective_cycle(recommendation=r,objective=objective(),governed_decision={"decision_id":"d1","workspace_id":"ws1","objective_id":"obj1","authority":"policy","decision":"allow"},source_run=source_run(),workforce=w,objective_context=context())
