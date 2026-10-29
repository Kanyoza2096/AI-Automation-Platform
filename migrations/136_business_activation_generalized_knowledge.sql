-- Business Activation generalized knowledge extension.
-- The proof slice (business_hours/refund_policy) remains valid; this migration
-- allows the same confirmed/versioned Knowledge Plane to represent broader
-- operator-provided business knowledge without creating a second store.

ALTER TABLE business_activation_facts
    DROP CONSTRAINT IF EXISTS business_activation_facts_fact_type_check;

ALTER TABLE business_activation_facts
    ADD CONSTRAINT business_activation_facts_fact_type_check
    CHECK (fact_type IN (
        'business_hours', 'refund_policy', 'business_identity',
        'business_description', 'product_service', 'location',
        'delivery_area', 'payment_method', 'contact_channel',
        'customer_segment', 'pricing', 'fulfillment', 'staff_role',
        'escalation_rule', 'integration', 'faq', 'business_rule',
        'business_knowledge'
    ));
