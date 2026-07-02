---
name: v10 platform spec
description: Where the master prompt lives and how the platform should be built
---

The full master prompt is in `attached_assets/My_AI_platform_1782987138601.docx`.

**Why:** User uploaded it twice — clear signal to proceed with the full rebuild.

**How to apply:** Build one milestone at a time in order:
1. Foundation & project structure ✅
2. Configuration, DI, and bootstrap
3. Core models and memory
4. AI abstraction and content engine
5. Rendering engine (Jinja2 + Playwright → PNG)
6. Facebook integration and scheduler
7. Code Guardian
8. REST API (versioned /api/v1/*)
9. Testing, documentation, and deployment

Keep v9 compatibility shim (`_register_v9_routes` in `application_factory.py`) active until each module is migrated.
