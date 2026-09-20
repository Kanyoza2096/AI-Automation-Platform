# Kanyoza Systems Bot — Environment Variables

## Required

| Variable | Description | Example |
|---|---|---|
| `GEMINI_KEY` | Google Gemini API key | `AIza...` |
| `PAGE_ACCESS_TOKEN` | Facebook Page access token | `EAA...` |
| `PAGE_ID` | Facebook Page ID | `123456789` |
| `VERIFY_TOKEN` | Facebook webhook verify token | `my-secret-token` |
| `APP_SECRET` | Facebook app HMAC secret (webhook signature verification) | `abc123...` |
| `SESSION_SECRET` | Flask session signing secret | any random string |

## Recommended

| Variable | Description | Default |
|---|---|---|
| `GEMINI_MODEL` | Gemini model to use | `gemini-2.5-pro` |
| `OWNER_PSID` | Your Messenger PSID | — |
| `HF_TOKEN` | Hugging Face API token | — |
| `SUPABASE_URL` | Supabase project URL | — |
| `SUPABASE_ANON_KEY` | Supabase anon key | — |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key | — |

## Code Guardian

| Variable | Description | Default |
|---|---|---|
| `GITHUB_USERNAME` | GitHub username | — |
| `GITHUB_TOKEN` | GitHub personal access token | — |
| `GUARDIAN_ENABLED` | Enable Code Guardian | `false` |
| `GUARDIAN_SCAN_HOURS` | Hours between scans | `24` |
| `GUARDIAN_EXCLUDE_REPOS` | Repos to skip (comma-separated) | — |

## Schedule

| Variable | Description | Default |
|---|---|---|
| `CONTENT_POSTING_HOURS_UTC` | Hours to post content | `4,7,10,14,17,19` |
| `NEWS_POSTING_HOURS_UTC` | Hours to post news | `6,13,18` |

## Server

| Variable | Description | Default |
|---|---|---|
| `PORT` | Server port | `5000` |
| `ENVIRONMENT` | Environment name | `production` |
| `LOG_LEVEL` | Logging level | `INFO` |

## Cloudflare Workers AI (image generation)

| Variable | Required | Notes |
|----------|----------|--------|
| `CLOUDFLARE_ACCOUNT_ID` | For CF images | Account ID from Cloudflare dashboard |
| `CLOUDFLARE_API_TOKEN` | For CF images | Token with Workers AI Run permission |

Free models used: FLUX.1-schnell, SDXL Lightning, SDXL base.
Also accepted via settings: `cloudflare_account_id`, `cloudflare_api_token`.

Without these, image path falls back to HuggingFace FLUX (needs `HF_TOKEN`) → Kroki → Picsum.


## Trend Intelligence

| Variable | Default | Notes |
|----------|---------|--------|
| `DEFAULT_WORKSPACE_ID` | `platform` | Soft filter for rules/posts when not `platform` |
| `TREND_SCAN_INTERVAL_SECONDS` | `300` | Scan cadence |
| `TREND_SEMANTIC_SCORE` | off | Gemini blend into relevance |
| `TREND_LLM_BRIEFING` | off | Gemini rewrite before publish request |
| `NEWSAPI_KEY` | — | NewsAPI adapter |
| `TWITTER_BEARER_TOKEN` | — | X/Twitter adapter |

## Web Intelligence
TAVILY_API_KEY=
BRAVE_API_KEY=
NEWSAPI_KEY=
TREND_WEB_VERIFY=false


## Tool agent
Uses Gemini/orchestrator + Web Intelligence. No extra keys beyond LLM + optional Tavily/Brave.


## Chat tool agent (Messenger)
| Variable | Default | Notes |
|----------|---------|--------|
| `CHAT_TOOL_AGENT` | true | Route current-fact questions through tool agent |
| `CHAT_TOOL_AGENT_IN_GEMINI` | false | Also route inside gemini.generate_chat_response |
