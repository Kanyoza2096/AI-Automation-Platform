import pytest
from services.v72_4_business_truth_adapters import BusinessTruthAdapters

class Q:
    def __init__(self, rows): self.rows=rows
    def select(self,*a): return self
    def eq(self,*a): return self
    def order(self,*a,**k): return self
    def limit(self,*a): return self
    def execute(self): return type('R',(),{'data':self.rows})()
class DB:
    def __init__(self, tables): self.tables=tables
    def table(self,n):
        if n not in self.tables: raise RuntimeError('missing table')
        return Q(self.tables[n])

def test_builds_durable_business_truth_without_authority():
    db=DB({
      'business_operating_objectives':[{'objective_id':'o1','objective':'grow','state':'active','envelope_id':'e1','updated_at':'x'}],
      'sales_opportunity_operating_state':[{'opportunity_id':'p1','stage':'proposal','updated_at':'x'}],
      'internal_business_work_state':[{'work_id':'w1','title':'sla','status':'open','priority':'high','updated_at':'x'}],
      'decisions':[{'decision_id':'d1','action':'notify','status':'approved','risk':'medium','created_at':'x'}],
      'agent_autonomous_runs':[{'run_id':'r1','agent_id':'a1','workflow_id':'w','status':'ready','disposition':'execution_candidate','updated_at':'x'}],
      'business_outcome_observations':[{'observation_id':'ob1','execution_id':'x','outcome_state':'observed','observed_at':'x'}],
      'business_outcome_attributions':[{'attribution_id':'at1','execution_id':'x','attribution_status':'attributed','created_at':'x'}],
      'business_decision_audit':[{'decision_id':'d1','event_type':'decision','risk':'medium','created_at':'x'}],
    })
    values=BusinessTruthAdapters('ws',db=db).build()
    assert values['objectives'].state=='LIVE'
    assert values['opportunities'].data['count']==1
    assert values['workforce'].data['count']==1
    for v in values.values():
        assert v.as_dict()['actionAuthorityGranted'] is False

def test_missing_store_is_explicit_unknown_not_fabricated():
    values=BusinessTruthAdapters('ws',db=DB({})).build()
    assert values['objectives'].state=='UNKNOWN'
    assert values['objectives'].data_class=='UNAVAILABLE'
    assert values['objectives'].data=={}
