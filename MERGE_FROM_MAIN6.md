# Merge: main (6) engines → elite converged architecture

## Source
- Base: Sprint 13 FULL converged (organism, species, council, pipeline, CP routes, Phase A–D)
- Donor: AI-Automation-Platform-main (6).zip

## Added from donor (missing only; no overwrite of convergence)
- Full `services/`: director, condition_engine, content_engine, meta_engine, gemini, facebook, supabase, …
- Kept converged: `services/publish_orchestration.py`, `services/publish_worker.py`
- Packages: repositories, scheduler, products, templates, static, utils, workflows
- Extra routes/workers files where absent
- Missing files under models/api/core/config/… only if not already present

## Preserved (elite architecture)
- council/ (canonical)
- organism/, species/
- domain/publishing/canonical_pipeline.py
- routes circulation/pulse/meta/cp_gap
- auth/audit/token convergence
- tests for isolation / pipeline / failure
