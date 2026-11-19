-- Least-privilege grants for autonomous knowledge review state.
-- The backend needs to read/create/update review cases and advance the sweep cursor;
-- it does not need to delete or truncate either table.
REVOKE ALL ON TABLE public.knowledge_evidence_review_cases FROM service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.knowledge_evidence_review_cases TO service_role;

REVOKE ALL ON TABLE public.knowledge_evidence_review_sweep_state FROM service_role;
GRANT SELECT, INSERT, UPDATE ON TABLE public.knowledge_evidence_review_sweep_state TO service_role;
