# Phase 4 — Products & honesty — DONE

## Industry domain models

Hospital and school were copy-pastes of church (baptism_date, sunday_service, tithe).

| Plugin | Fixed models |
|--------|----------------|
| **hospital** | Patient (ward, blood_type, outpatient); visit-style Attendance; payment Donation; clinic Event |
| **school_mis** | Student (grade, class, guardian); class Attendance; tuition Donation; school Event |
| **church_mis** | Unchanged (correct domain) |
| **crm** | Already domain-shaped |

Repository defaults: `consultation` / `class_period` / `tuition` instead of `sunday_service` / `tithe`.

## Duplicates / legacy

- `products/church_mis/models.py` → re-exports `plugins.industries.church_mis.models` (deprecated)
- `products/converter` → Flask Blueprint, **not mounted** on FastAPI (documented in module docstring)

## README

Root `README.md` now describes:

- Real stack (3.12 / FastAPI)
- Quick start + env
- SERVICE_MODE table
- Layout and industry plugins
- Phase 0–4 outcomes

## Suggested later (not blocking)

- Port converter to FastAPI or delete `products/converter`
- Remove `flask` from requirements once converter is gone
- Split `api/v1.py` into routers
- Enable and document Supabase RLS policies

## System status

Phases **0–4 complete** on this tree. The designed core path and engines can load and run with proper env; remaining work is operational (keys, RLS, UI) and incremental product depth.
