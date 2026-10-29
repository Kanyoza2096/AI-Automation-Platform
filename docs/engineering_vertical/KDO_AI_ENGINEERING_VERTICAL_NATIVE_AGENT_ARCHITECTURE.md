# KDO AI Engineering Vertical — Native Agent Architecture

## Architectural direction

KDO's engineering vertical should follow the established product pattern used by native AI software engineering platforms: a project-aware agent core, planning before execution, specialist work, persistent task state, bounded tools, iterative test/review loops, and a user-visible review/approval boundary. Replit documents a Plan Mode separated from Build Mode and task-board workflows; Devika documents a planner/researcher/coder/action/runner/patcher/reporting agent loop. KDO adopts these general patterns while preserving its own architecture, policies, and implementation. This is design alignment, not a claim of identical internals or endorsement by either project.

## Canonical KDO flow

1. Accept the user request and identify the project/workspace.
2. Inspect repository state and relevant project instructions.
3. Generate a typed task plan with dependencies and acceptance criteria.
4. Validate roles, task contracts, dependency graph, scope, and policy references.
5. Present a plan/review checkpoint when the product workflow or risk level requires it.
6. Build bounded task-specific context.
7. Assemble a prompt from a stable versioned role template, shared policy, validated task contract, relevant context, and output schema.
8. Invoke the existing provider-neutral LLM gateway.
9. Apply proposed edits only through the governed execution boundary and isolated workspace.
10. Run machine-observed tests and checks; retry or replan within configured limits when appropriate.
11. Obtain independent review and any required human/governance approval.
12. Present a diff, test evidence, and result; preserve durable audit records and checkpoints.

## Prompt architecture

`services/kdo_engineering_prompt_assembler.py` implements the first assembly boundary. `EngineeringTaskAssignment` is a typed assignment contract; `RoleTemplate` contains stable role instructions; `EngineeringPromptAssembler` composes deterministic prompts and returns a manifest with hashes and version identifiers. The gateway adapter calls this assembler and attaches its manifest to returned model-reported evidence.

Prompt assembly does not grant authority. Tool availability, workspace boundaries, network access, file writes, shell execution, deployment, and approval must remain enforced by the runtime authorization and execution systems.

## Role template policy

Role templates are stable, versioned defaults. Task objective, dependencies, acceptance criteria, deliverables, policy references, and bounded context vary per assignment. Specialist output is a proposal until validated. Agents may request clarification or report blockers; they may not silently change acceptance criteria or expand their own permissions.

## Evidence levels

- Model-reported: explanations and proposed evidence from the LLM.
- Machine-observed: test output, exit codes, patch digests, repository state, static-analysis results, and reproducible checks.
- Governed execution: authoritative authorization decisions, durable execution records, and receipts.

A prompt manifest hash supports reproducibility; it does not prove a model's claims are true. Only the appropriate evidence type can satisfy an acceptance criterion.

## Product behaviors to evolve toward

- Plan and Build modes with an explicit no-mutation planning state.
- Task board states such as Draft, Active, Ready for Review, and Done.
- Isolated task workspaces/branches and checkpoints before risky changes.
- Context-aware research and repository search.
- Iterative run/test/fix loops with bounded retries.
- Visible diffs, verification results, and review controls.
- Persistent project/task state across user follow-ups.
- Model/provider configuration per task where supported, without changing the stable role contract.

These are target behaviors; they must not be treated as already shipped merely because they are documented here.

## Compatibility and limits

The first milestone is intentionally incremental: it adds validated task-specific prompt assembly and an audit manifest while retaining the current gateway and coordinator. The current coordinator's fixed planner → implementer → test engineer → reviewer sequence is not yet a general dependency-graph scheduler. Full platform parity requires subsequent work on dynamic DAG planning, isolated worktrees/sandboxes, task-board UI, resumable background jobs, patch/checkpoint management, and live staging integration tests.
