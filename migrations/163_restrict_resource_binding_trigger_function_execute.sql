-- U2: remove direct invocation rights from the resource-binding trigger function.
-- PostgreSQL checks EXECUTE when CREATE TRIGGER is issued; trigger firing does
-- not require callers to retain EXECUTE. Keep the function owner-controlled.
REVOKE EXECUTE ON FUNCTION public.validate_integration_connection_resource_binding()
    FROM PUBLIC, anon, authenticated, service_role;
