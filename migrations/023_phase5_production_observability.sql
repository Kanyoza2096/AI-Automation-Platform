-- Phase 5 migration slot.
--
-- This release intentionally contains no schema change for phase 5, but the
-- migration number is reserved so the migration chain remains contiguous.
-- Keeping an explicit, idempotent migration record makes from-zero bootstrap
-- deterministic and prevents deployment tooling from silently skipping a
-- version.

DO $$
BEGIN
    PERFORM 1;
END $$;
