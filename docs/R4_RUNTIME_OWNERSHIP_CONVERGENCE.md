# R4 — Runtime Ownership Convergence

## Objective

Make runtime ownership explicit and mechanically certifiable after the R3
legacy-executor fence.

## Canonical execution law

`ActionIntent -> Governance -> Authorization -> Action Kernel -> Connector -> Provider -> Receipt -> Verification -> Reconciliation`

## R4 boundary

The legacy natural-language `CommandExecutor` is a compatibility surface, not
an execution authority.

- GET actions may remain compatibility reads.
- Built-in POST/mutation actions fail closed with `governance_required`.
- Plugin mutations already require an explicit `action_kernel` execution mode
  and a declared `governed_action_type`.
- No provider credential or direct provider transport is added to the legacy
  executor.
- The Action Intent bridge and canonical runtime route anchors are certified
  from the actual source tree.

## Certification

`services/runtime_ownership_certification.py` performs source-level checks for:

1. no `requests.post` mutation transport in `CommandExecutor`;
2. explicit Action Kernel authority for fenced built-in mutations;
3. mounted canonical Action Intent bridge;
4. presence of the canonical runtime route anchors.

This module is an audit instrument only. It does not execute actions and does
not create a second authority plane.
