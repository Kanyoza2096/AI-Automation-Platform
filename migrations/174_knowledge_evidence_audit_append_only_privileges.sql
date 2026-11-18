-- Tighten the audit tables against inherited/default service-role grants.
-- The review case queue is mutable by the service layer; the event ledgers are not.
REVOKE ALL PRIVILEGES ON TABLE
    public.knowledge_evidence_review_events,
    public.knowledge_evidence_promotion_events
FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT, INSERT ON TABLE
    public.knowledge_evidence_review_events,
    public.knowledge_evidence_promotion_events
TO service_role;

COMMENT ON TABLE public.knowledge_evidence_review_events IS
    'Append-only audit ledger. service_role may read and append; update, delete, and truncate are revoked.';
COMMENT ON TABLE public.knowledge_evidence_promotion_events IS
    'Append-only promotion/revocation ledger. service_role may read and append; update, delete, and truncate are revoked.';
