# Website Connector — Governed Public Content

KDO now treats a business website/CMS as a first-class connector alongside Facebook, WhatsApp, Telegram, LinkedIn, Twitter/X and Instagram.

## Execution contract

```text
Event
  → Rule
  → Decision / Approval
  → Action Kernel
  → Website Connector
  → Provider Receipt
  → Reconciliation
  → Outcome / Attribution
```

The website connector is deliberately **CMS-agnostic**. It does not assume WordPress, a particular frontend, or a particular backend. The website/CMS exposes a small REST API and KDO supplies the adapter configuration.

## Connection model

Create a normal `social_accounts` connection with:

```json
{
  "platform": "website",
  "platform_account_id": "main-site",
  "account_name": "Business Website",
  "access_token": "<encrypted CMS bearer token>",
  "platform_config": {
    "base_url": "https://cms.example.com/api",
    "publish_path": "/content",
    "update_path": "/content/{post_id}",
    "lookup_path": "/content/{post_id}",
    "health_path": "/health",
    "auth_mode": "bearer",
    "content_type": "article"
  }
}
```

For API-key authentication use:

```json
"platform_config": {
  "base_url": "https://cms.example.com/api",
  "auth_mode": "api_key",
  "api_key": "<secret>",
  "api_key_header": "X-API-Key"
}
```

`https` is required by default. HTTP is available only when `allow_insecure_http=true` is explicitly configured for a controlled development environment.

## Provider API contract

### Publish

`POST {base_url}{publish_path}`

Example body:

```json
{
  "content_type": "news",
  "title": "KDO launches governed AI automation",
  "slug": "kdo-governed-ai-automation",
  "excerpt": "A short public summary",
  "content": "Full article body...",
  "status": "publish",
  "featured_image_url": "https://cdn.example.com/image.jpg",
  "category": "Company News",
  "tags": ["AI", "Automation"],
  "canonical_url": "https://example.com/news/kdo-governed-ai-automation"
}
```

### Update

`PATCH {base_url}{update_path}` with `{post_id}` substituted.

The connector also accepts `PUT` when `update_method` is explicitly configured.

### Receipt requirement

A 2xx response **without a provider content ID is not success**. KDO records it as `uncertain` so reconciliation can determine whether the provider committed the mutation.

Supported ID fields include `id`, `post_id`, `content_id`, `article_id`, `page_id`, `object_id`, and the same fields nested under `data` or `result`.

Transport timeout/network ambiguity is also `uncertain`.

## Governed actions

Two Action Kernel contracts are registered:

- `website_publish` — high risk, approval required
- `website_update` — high risk, approval required

They require an exact workspace-scoped `social_account_id`, so a website connection from another tenant cannot be used.

There is intentionally **no website delete action** in this connector yet. Public-content retirement should be designed separately because deletion has different recovery and audit semantics.

## Direct API admission

The API exposes durable, approval-gated admission endpoints:

```text
POST /api/v1/website-content/publish
POST /api/v1/website-content/update
```

These endpoints do not call the CMS directly. They create Action Kernel intents.

### Publish request

```json
{
  "workspace_id": "ws_123",
  "social_account_id": "website_123",
  "content_type": "news",
  "title": "Company announcement",
  "slug": "company-announcement",
  "excerpt": "Short summary",
  "content": "Full public article...",
  "featured_image_url": "https://cdn.example.com/a.jpg",
  "category": "News",
  "tags": ["Company", "News"],
  "idempotency_key": "website-news-company-announcement-v1"
}
```

The response should normally be `approval_required` until the governed approval flow approves the ActionIntent.

### Update request

```json
{
  "workspace_id": "ws_123",
  "social_account_id": "website_123",
  "post_id": "article-123",
  "title": "Updated announcement",
  "content": "Corrected public information...",
  "idempotency_key": "website-update-article-123-v2"
}
```

## Automation events

The business automation catalog now includes:

- `website.content.publish.approved` → `website_publish`
- `website.content.update.approved` → `website_update`

These are intentionally named **approved** events. A generic content-generation event must not be treated as permission to publish publicly.

## What this enables

The connector is designed for:

- news
- company announcements
- blog posts
- public knowledge articles
- product/service information
- public updates
- corrections to existing content
- scheduled/automated publication after governance approval

The connector does **not** generate content itself. Content intelligence, AI agents, business rules, approvals and policy remain upstream. The connector is the controlled external side-effect adapter.
