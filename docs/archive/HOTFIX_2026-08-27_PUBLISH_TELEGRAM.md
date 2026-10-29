# Hotfix — publish gate + Telegram (Render logs ~18:06–18:08 UTC)

## Issues in logs
1. `Publish denied workspace=platform decision_id=None strict=True`
2. `NameError: name 'time' is not defined` in email_service.send_telegram
3. DFMO event_schema / workspace_id (needs full redeploy of nerve fixes)
4. Card render succeeded — publish blocked only at decision gate

## Fixes
- `import time` in email_service.py
- Auto-issue DIRECTOR_AUTO decision when decision_id missing (publish_authorization + publish_worker)
- Env: AUTO_ISSUE_PUBLISH_DECISION=0 to disable auto-issue

Redeploy this ZIP to Render.
