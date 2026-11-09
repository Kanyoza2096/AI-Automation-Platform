-- U2: persist the tenant resource binding for connector connections and certification.
-- Legacy rows remain unbound until an authenticated operator binds them explicitly.

ALTER TABLE public.integration_connections
    ADD COLUMN IF NOT EXISTS brand_id TEXT,
    ADD COLUMN IF NOT EXISTS social_account_id TEXT;

ALTER TABLE public.connector_certification_registry
    ADD COLUMN IF NOT EXISTS brand_id TEXT,
    ADD COLUMN IF NOT EXISTS social_account_id TEXT;

ALTER TABLE public.integration_connections
    DROP CONSTRAINT IF EXISTS integration_connections_resource_binding_pair_check;
ALTER TABLE public.integration_connections
    ADD CONSTRAINT integration_connections_resource_binding_pair_check
    CHECK (
        (brand_id IS NULL AND social_account_id IS NULL)
        OR (brand_id IS NOT NULL AND social_account_id IS NOT NULL)
    );

ALTER TABLE public.connector_certification_registry
    DROP CONSTRAINT IF EXISTS connector_certification_resource_binding_pair_check;
ALTER TABLE public.connector_certification_registry
    ADD CONSTRAINT connector_certification_resource_binding_pair_check
    CHECK (
        (brand_id IS NULL AND social_account_id IS NULL)
        OR (brand_id IS NOT NULL AND social_account_id IS NOT NULL)
    );

CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_connections_resource_binding
    ON public.integration_connections(workspace_id, id, brand_id, social_account_id);

-- Enforce the resource identity against the canonical tenant tables as well.
-- NOT VALID preserves historical rows while checking all new writes/updates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_brands_workspace_id_id
    ON public.brands(workspace_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_workspace_brand_id_id
    ON public.social_accounts(workspace_id, brand_id, id);

ALTER TABLE public.integration_connections
    DROP CONSTRAINT IF EXISTS integration_connections_brand_workspace_fk;
ALTER TABLE public.integration_connections
    ADD CONSTRAINT integration_connections_brand_workspace_fk
    FOREIGN KEY (workspace_id, brand_id)
    REFERENCES public.brands(workspace_id, id)
    ON DELETE RESTRICT
    ON UPDATE RESTRICT
    NOT VALID;

ALTER TABLE public.integration_connections
    DROP CONSTRAINT IF EXISTS integration_connections_social_account_binding_fk;
ALTER TABLE public.integration_connections
    ADD CONSTRAINT integration_connections_social_account_binding_fk
    FOREIGN KEY (workspace_id, brand_id, social_account_id)
    REFERENCES public.social_accounts(workspace_id, brand_id, id)
    ON DELETE RESTRICT
    ON UPDATE RESTRICT
    NOT VALID;

ALTER TABLE public.connector_certification_registry
    DROP CONSTRAINT IF EXISTS connector_certification_resource_binding_fk;
ALTER TABLE public.connector_certification_registry
    ADD CONSTRAINT connector_certification_resource_binding_fk
    FOREIGN KEY (workspace_id, connection_id, brand_id, social_account_id)
    REFERENCES public.integration_connections(workspace_id, id, brand_id, social_account_id)
    ON DELETE RESTRICT
    NOT VALID;

CREATE OR REPLACE FUNCTION public.validate_integration_connection_resource_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    brand_workspace TEXT;
    account_workspace TEXT;
    account_brand TEXT;
BEGIN
    IF NEW.brand_id IS NULL AND NEW.social_account_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.brand_id IS NULL OR NEW.social_account_id IS NULL THEN
        RAISE EXCEPTION 'integration_connection_resource_binding_pair_required';
    END IF;

    SELECT b.workspace_id
      INTO brand_workspace
      FROM public.brands AS b
     WHERE b.id = NEW.brand_id;

    IF NOT FOUND OR brand_workspace <> NEW.workspace_id THEN
        RAISE EXCEPTION 'integration_connection_brand_workspace_mismatch';
    END IF;

    SELECT a.workspace_id, a.brand_id
      INTO account_workspace, account_brand
      FROM public.social_accounts AS a
     WHERE a.id = NEW.social_account_id;

    IF NOT FOUND
       OR account_workspace <> NEW.workspace_id
       OR account_brand <> NEW.brand_id THEN
        RAISE EXCEPTION 'integration_connection_social_account_binding_mismatch';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_integration_connection_resource_binding
    ON public.integration_connections;
CREATE TRIGGER trg_integration_connection_resource_binding
    BEFORE INSERT OR UPDATE OF workspace_id, brand_id, social_account_id
    ON public.integration_connections
    FOR EACH ROW
    EXECUTE FUNCTION public.validate_integration_connection_resource_binding();

COMMENT ON COLUMN public.integration_connections.brand_id IS
    'Optional explicit brand binding; required with social_account_id for account-scoped connector health.';
COMMENT ON COLUMN public.integration_connections.social_account_id IS
    'Optional explicit social account binding; validated against workspace and brand.';
COMMENT ON COLUMN public.connector_certification_registry.brand_id IS
    'Must match the bound integration connection brand when resource-bound.';
COMMENT ON COLUMN public.connector_certification_registry.social_account_id IS
    'Must match the bound integration connection social account when resource-bound.';
