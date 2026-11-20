# GrowthBridge Virtual legacy source recovery (read-only)

Inspection date: 2026-11-20

## Findings

The GrowthBridge Virtual brand has 10 legacy knowledge entries:

- One user-approved brand-configuration record. It is not website-fact evidence and must remain configuration.
- Eight service-description records, each with a stable service ID and a source slug.
- One historical website-context snapshot fetched 2026-11-14. Its embedded source update time is 2026-09-02; the record is explicitly marked `untrusted_business_context` and `stale_review_required`.

The eight service records identify these exact slugs:

| Knowledge entry | Service slug |
|---|---|
| `knowledge.growthbridgevirtual.services.analytics.v1` | `data-analytics-reporting` |
| `knowledge.growthbridgevirtual.services.brand-identity.v1` | `brand-identity-positioning` |
| `knowledge.growthbridgevirtual.services.business-consulting.v1` | `business-consulting-growth-advisory` |
| `knowledge.growthbridgevirtual.services.digital-marketing.v1` | `digital-marketing-strategy` |
| `knowledge.growthbridgevirtual.services.paid-advertising.v1` | `paid-advertising-performance-marketing` |
| `knowledge.growthbridgevirtual.services.seo-content.v1` | `seo-content-marketing` |
| `knowledge.growthbridgevirtual.services.social-media.v1` | `social-media-management` |
| `knowledge.growthbridgevirtual.services.web-development.v1` | `web-design-development` |

All eight records describe service capabilities and explicitly warn against treating benefits as verified customer outcomes or performance claims.

## Why canonical URLs are still unresolved

The configured KDO website integration is named `growthbridge-production`, with backend API base URL `https://growthbridgevirtual.onrender.com` and publishing path `/api/kdo/content`. That is the integration API, not verified proof of the public marketing-site origin or the service-page route pattern. The stored service slugs do not establish either value.

The historical website snapshot contains organization, service, SEO, and article context but no canonical public website URL. The integration event records inspected contain synthetic test-blog events, not public service-page URLs.

Therefore this recovery does **not** construct URLs such as `/services/{slug}`, does not treat the Render API hostname as the public site, and does not configure a trusted source host. The next evidence needed is the owner-confirmed public website origin or an authenticated source inventory that includes canonical page URLs. Once obtained, each exact page must be fetched and captured before the existing reviewer can adjudicate the associated service record.

## Governance boundary

- The brand-profile record is user-approved configuration, not public-fact evidence.
- Service descriptions can support only the capabilities actually stated on their corresponding published pages.
- Benefits are not evidence of measured ROI, rankings, traffic, audience growth, or client results.
- The historical website snapshot is stale and remains contextual only until refreshed.
- No database row, source URL, trust class, review decision, or promotion state was changed during this inspection.
