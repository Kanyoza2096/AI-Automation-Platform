# Kanyoza P11 — Director and Canonical Topology Parity

## Finding

Director's `SystemTopology` had its own list of nodes and dependency edges. It copied canonical dependency edges only when both the dependent and dependency were already present in Director's manually maintained node list. Canonical nodes such as `gemini`, `github`, `job_stats_service`, `autonomous_content`, `autonomous_loop`, and `scheduler` could therefore be missing from Director's graph even though Meta Engine and the topology API declared them.

## Change

- `services/director/analysis.py`
  - Director now adds any missing canonical nodes before applying edges.
  - Existing sector/criticality metadata is preserved for nodes already defined by Director.
  - Missing nodes receive conservative metadata defaults; dependency edges always come from `CANONICAL_ENGINE_DEPENDENCIES`.
  - Forward `depends_on` and reverse `depended_by` edges are both rebuilt from the same canonical declaration.
- Added a parity test requiring exact node-set equality and exact dependency/reverse-edge agreement with the canonical graph.

## Validation

- Compilation of changed module and test file: passed.
- Focused Director / topology / route / health-truth suites: **117 passed**.
- Full repository suite remains uncertified green due to the known missing `supabase` dependency in the publishing pipeline test environment.

## Effect

Director's dependency analysis can now represent the full declared engine topology instead of silently omitting canonical nodes. This aligns the graph structure; it does not imply every node has a live runtime instance or a healthy state.
