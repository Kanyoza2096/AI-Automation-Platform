# KDO Director — Target Architecture and Repository Integration Map

**Status:** target architecture contract  
**Scope:** KDO platform-level Director and its integration with existing organism, intelligence, governance, objective, and workforce components.  
**Implementation rule:** extend and connect existing canonical components before introducing new subsystems.

## 1. Mission

The Director is KDO's platform-level strategic intelligence and executive decision authority. It is responsible for understanding what the organism is trying to become, evaluating the evidence about its current state, deciding what matters most, delegating work to the correct subsystem, governing the decision path, and evaluating whether the intended outcome was achieved.

The Director is not:
- a health dashboard with an LLM summary;
- a generic task generator;
- a worker that directly performs every operation;
- an alternate publishing scheduler;
- an authority that can bypass workspace governance, Council, DecisionGate, Action Kernel, or human approval.

## 2. Six responsibilities

1. **Strategic intelligence:** maintain an evidence-grounded view of KDO objectives, capabilities, dependencies, limitations, risks, opportunities, and priorities.
2. **Evidence-based reasoning:** separate observed facts, source-reported claims, inferences, assumptions, conflicts, and unknowns. Every consequential claim must be traceable.
3. **Decision-making and coordination:** select priorities, decompose objectives, form dependency-aware plans, delegate to subsystem owners, and track plan progress.
4. **Governed autonomy:** act only through explicit capability authority, policy, Council/DecisionGate, approval, execution admission, audit, and rollback boundaries.
5. **Continuous learning:** evaluate verified outcomes, identify recurring patterns, adapt priorities, and measure whether decisions improve results.
6. **Responsibility separation:** coordinate specialist subsystems without taking over their operational workflows. Brand schedulers own content generation and publishing; Director may observe, prioritize, and delegate diagnosis, but must not directly trigger publishing.

## 3. Hybrid intelligence model

The Director uses three cooperating planes:

### 3.1 Agentic reasoning plane
Use bounded AI reasoning for:
- interpreting strategic objectives;
- diagnosing ambiguous or cross-subsystem conditions;
- comparing alternatives and trade-offs;
- decomposing objectives into tasks and dependencies;
- adapting plans when verified outcomes differ from expectations.

AI-generated plans and claims remain proposals until schema validation, evidence checks, capability ownership checks, and governance admission succeed.

### 3.2 Deterministic execution plane
Use deterministic services for:
- state transitions and typed event contracts;
- evidence persistence and provenance;
- task/mission identity and dependency validation;
- idempotency, retries, receipts, and outcome reconciliation;
- policy evaluation, execution admission, audit, and rollback.

The reasoning model does not replace these mechanisms.

### 3.3 Governance and control plane
Enforce:
- workspace and tenant scope;
- capability permissions and autonomy levels;
- risk classification and Council requirements;
- human authorization for sensitive or irreversible operations;
- DecisionGate and canonical Action Kernel handoff;
- kill switch, audit, and rollback.

No recommendation, learning signal, or model confidence value grants execution authority.

## 4. Existing repository components to reuse

| Target responsibility | Existing component | Current integration status |
|---|---|---|
| Platform observation | Director observers, `organism_state()`, `decision_context()`, Meta Engine, health/event signals | Present; source authority and durable cross-system world model remain incomplete |
| Decision evidence | `Decision`, `director_decisions` store, evidence fields from PR #135 | Structured records and startup restoration added; runtime log confirmation still required |
| Health-report truth | Director health/reporting from PR #134 | Evidence gaps and non-nominal observers are exposed rather than reported as issue-free |
| Capability ownership/readiness | Director capability catalog, runtime binding report, `services/director_bindings.py` | Present as wiring/contract diagnostics; binding is not proof of downstream runtime readiness |
| Governance | Director Policy, Council integration, proposal handoff, Action Kernel, authority fence | Present in multiple paths; end-to-end convergence and cognitive-plane integration remain open |
| Workspace cognitive authorization | `organism.cognitive.CognitiveDecisionPlane`, `DecisionGate`, `organism.goals.Goal` | Exists as a separate canonical boundary; Director is not yet wired to it |
| Intelligence decision construction | `services/intelligence_decision_engine.py`, evidence graph, AI fabric, learning decision context | Exists; Director does not currently consume it as its strategic decision-construction path |
| Mission dependencies | Director mission graph and dependency-aware task scheduler | Present, but not a complete durable objective planner |
| Objective outcome verification | IPC-14 objective outcome learning | Exists as a separate objective-bound verification contract |
| Objective replanning | IPC-15 | Exists as an advisory, governance-separated replanning contract |
| Objective cycle admission | IPC-16 and durable workforce admission | Exists; Director orchestration into this lifecycle remains open |
| Learning | Director LearningLoop and persisted learning replay | PR #136 separates verified success, failure, and unverified completion; broader outcome calibration remains open |

## 5. Current gaps confirmed by source audit

1. Director `decide()` is primarily rule-based: health score, reported issues, accepted engine signals, and a static goal-to-capability mapping.
2. `_build_plan_async()` currently wraps the returned task list and decision context. It does not yet perform strategic objective decomposition into a validated, durable plan DAG.
3. `generate_strategic_outlook()` uses simple task-volume and health-score trends. It is not yet a robust strategic forecasting or opportunity-evaluation system.
4. Director's local `Goal` model is simpler than the canonical workspace-scoped `organism.goals.Goal` contract, which already carries authority, constraints, budget, lifecycle state, and deadline.
5. The Director does not currently reference `CognitiveDecisionPlane` or `IntelligenceDecisionEngine` in its core orchestration path. These should be integrated at the correct platform/workspace boundary, not duplicated.
6. IPC-14/15/16 exist but are not yet the Director's canonical objective outcome → replan → governed next-cycle loop.
7. Existing Council and Action Kernel paths must be reconciled into one explicit authorization and execution boundary; no parallel executor should be introduced.
8. Learning is now more truthful about unverified completion, but confidence calibration, outcome attribution, pattern robustness, and long-term strategy evaluation remain open.

## 6. Implementation sequence

### Gate A — Trustworthy perception and decision evidence
- Preserve source identity, timestamps, freshness, conflicts, and unknown states.
- Persist structured decisions and restore the audit trail.
- Ensure reports distinguish observer status from issue-record counts.
- Do not claim completion until staging persistence/restart behavior is verified.

### Gate B — Strategic objective bridge
- Map platform-level objectives to canonical workspace-scoped organism goals without losing ownership or tenant boundaries.
- Define how Director objectives differ from workspace organism goals.
- Carry objective identity, constraints, budget, deadline, authority, and success criteria through delegation.
- Reject cross-workspace references and ambiguous ownership.

### Gate C — Evidence-grounded hybrid reasoning
- Connect Director decision context to the existing intelligence evidence graph and decision-construction contracts.
- Use bounded AI interpretation and planning; keep learning reference-only until outcome attribution and calibration meet explicit acceptance criteria.
- Validate structured plans and claims deterministically before governance.

### Gate D — Durable mission planning and coordination
- Extend the existing dependency graph into objective-linked plan DAGs.
- Validate cycles, missing capabilities, ownership, workspace scope, prerequisites, deadlines, and success criteria.
- Track plan lineage, step states, retries, cancellation, recovery, and explainable replanning.
- Delegate to registered specialist owners; do not implement their domain workflows inside Director.

### Gate E — Unified governance and execution convergence
- Route consequential actions through one canonical governance decision path.
- Integrate Council, DecisionGate, capability policy, workforce admission, Action Kernel receipts, idempotency, and rollback.
- Preserve suggest-only behavior for untrusted or engine-originated recommendations.
- Verify that no legacy direct handler path bypasses governance.

### Gate F — Verified outcome learning and adaptation
- Learn positive capability reliability only from verified outcomes.
- Keep handler-reported completion as unverified evidence.
- Use IPC-14 outcome verification, IPC-15 replan recommendations, and IPC-16 governed next-cycle admission where applicable.
- Require sufficient sample size, uncertainty bounds, and measurable improvement before adapting priorities.

### Gate G — Strategic evaluation and operational proof
- Maintain a benchmark suite for diagnosis, evidence classification, priority selection, decomposition, delegation, replanning, governance, and outcome verification.
- Run adversarial cases: stale evidence, conflicting observers, missing telemetry, false success receipts, failed dependencies, workspace mismatch, duplicate events, and forbidden publishing actions.
- Prove staging behavior across restarts and controlled integration scenarios before considering production authorization.

## 7. Definition of done for the mature Director

The Director is not considered mature merely because it can generate plans or reports. It must demonstrate that it can:

- explain the strategic objective and current evidence state;
- distinguish observation, inference, assumption, conflict, and unknown;
- identify the right subsystem owner and delegate within that owner's authority;
- produce a valid dependency-aware plan with measurable success criteria;
- obtain the required governance authorization before execution;
- track execution with durable identity and evidence;
- verify outcomes independently rather than trusting handler acknowledgements;
- adapt only when supported by attributable outcome data;
- explain why its priorities changed and show measurable improvement;
- preserve workspace boundaries and subsystem responsibility separation.

## 8. Delivery policy

- All routine engineering changes target the `staging` branch and staging Render service first.
- Production remains untouched unless explicitly authorized separately.
- Do not add migrations or incur additional Supabase costs without explicit approval.
- A PR is not complete when code is merely merged: CI, staging deployment, startup/runtime logs, and the relevant behavior must be checked.
- Existing components are reused where they satisfy the contract; new abstractions require a documented gap and a tested integration boundary.
- Phase completion is based on acceptance evidence, not elapsed weeks or PR counts.
