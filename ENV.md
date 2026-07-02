# Kanyoza Systems Bot — Environment Variables

## Required

| Variable | Description | Example |
|---|---|---|
| `GEMINI_KEY` | Google Gemini API key | `AIza...` |
| `PAGE_ACCESS_TOKEN` | Facebook Page access token | `EAA...` |
| `PAGE_ID` | Facebook Page ID | `123456789` |
| `VERIFY_TOKEN` | Facebook webhook verify token | `my-secret-token` |

## Recommended

| Variable | Description | Default |
|---|---|---|
| `GEMINI_MODEL` | Gemini model to use | `gemini-2.5-pro` |
| `APP_SECRET` | Facebook app secret | — |
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
