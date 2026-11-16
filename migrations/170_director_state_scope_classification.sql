-- Director state scope classification.
-- Existing rows are preserved as legacy_unclassified; ownership is never guessed.
ALTER TABLE public.director_tasks
    ADD COLUMN IF NOT EXISTS scope_type TEXT;
ALTER TABLE public.director_observations
    ADD COLUMN IF NOT EXISTS scope_type TEXT;

UPDATE public.director_tasks
SET scope_type = CASE
    WHEN workspace_id IS NOT NULL AND btrim(workspace_id) <> '' THEN 'workspace'
    ELSE 'legacy_unclassified'
END
WHERE scope_type IS NULL;

UPDATE public.director_observations
SET scope_type = CASE
    WHEN workspace_id IS NOT NULL AND btrim(workspace_id) <> '' THEN 'workspace'
    ELSE 'legacy_unclassified'
END
WHERE scope_type IS NULL;

ALTER TABLE public.director_tasks
    ALTER COLUMN scope_type SET DEFAULT 'platform';
ALTER TABLE public.director_observations
    ALTER COLUMN scope_type SET DEFAULT 'platform';

ALTER TABLE public.director_tasks
    ADD CONSTRAINT director_tasks_scope_type_check
    CHECK (scope_type IN ('platform', 'workspace', 'legacy_unclassified'));
ALTER TABLE public.director_tasks
    ADD CONSTRAINT director_tasks_workspace_scope_check
    CHECK (
        (scope_type = 'workspace' AND workspace_id IS NOT NULL AND btrim(workspace_id) <> '')
        OR (scope_type IN ('platform', 'legacy_unclassified') AND workspace_id IS NULL)
    );

ALTER TABLE public.director_observations
    ADD CONSTRAINT director_observations_scope_type_check
    CHECK (scope_type IN ('platform', 'workspace', 'legacy_unclassified'));
ALTER TABLE public.director_observations
    ADD CONSTRAINT director_observations_workspace_scope_check
    CHECK (
        (scope_type = 'workspace' AND workspace_id IS NOT NULL AND btrim(workspace_id) <> '')
        OR (scope_type IN ('platform', 'legacy_unclassified') AND workspace_id IS NULL)
    );

CREATE INDEX IF NOT EXISTS director_tasks_workspace_created_idx
    ON public.director_tasks (workspace_id, created_at DESC)
    WHERE scope_type = 'workspace';
CREATE INDEX IF NOT EXISTS director_observations_workspace_timestamp_idx
    ON public.director_observations (workspace_id, "timestamp" DESC)
    WHERE scope_type = 'workspace';

COMMENT ON COLUMN public.director_tasks.scope_type IS
    'Explicit state ownership: workspace, platform, or legacy_unclassified. Legacy ownership is never inferred.';
COMMENT ON COLUMN public.director_observations.scope_type IS
    'Explicit evidence ownership: workspace, platform, or legacy_unclassified. Legacy ownership is never inferred.';
