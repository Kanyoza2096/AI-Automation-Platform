# Business Activation — Generalized Business Knowledge

## Purpose

The two original fact types (`business_hours` and `refund_policy`) remain the certified proof slice. They are not the product ceiling.

R10 adds a broad natural-language activation mode using `business_knowledge`.

An operator can describe the business naturally. KDO extracts specialized knowledge when it recognizes a structure and preserves other explicit statements as reviewable business knowledge instead of silently dropping them.

## Supported knowledge families

- business identity
- business description
- products and services
- locations
- delivery areas
- payment methods
- contact channels
- customer segments
- pricing
- fulfillment
- staff roles
- escalation rules
- integrations
- FAQs
- business rules
- general business knowledge
- existing business hours
- existing refund/return policy

This list is an ontology of recognized structure, not a restriction on what the operator is allowed to say.

## Safety boundary

Natural-language understanding does not mean automatic truth.

The extractor:

- preserves exact `source_span` evidence;
- never invents missing values;
- keeps speculative statements out of confirmed proposals;
- assigns a conservative confidence to generic statements;
- requires explicit operator confirmation before Knowledge Plane use;
- keeps versioning/supersession in the existing Knowledge Plane;
- does not create execution authority.

## Architecture

```text
Natural-language business description
            ↓
Business Activation broad extraction
            ↓
Recognized structured facts + generic business statements
            ↓
Clarification / Review
            ↓
Explicit operator confirmation
            ↓
Existing Knowledge Plane
            ↓
Runtime reads only confirmed current knowledge
```

## Example

Input:

> We are a furniture shop in Blantyre. We sell locally made sofas and tables. Customers can pay with Airtel Money. We deliver within Blantyre and Zomba. Our team can arrange urgent deliveries by phone.

Expected proposed knowledge includes:

- business identity
- product/service
- payment method
- delivery area
- escalation/business-rule or contact-related knowledge where applicable

Every proposal retains the exact operator text span that produced it.

## Non-goals

R10 does not claim arbitrary-world knowledge extraction accuracy. Unsupported or ambiguous statements remain reviewable/unparsed rather than being fabricated.

R10 also does not change the Action Kernel, Connector Fabric, authorization model, or Command Center authority boundary.
