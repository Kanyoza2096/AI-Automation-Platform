# Phase A override for 008_hardening.sql

Do **not** apply the original lines:

```sql
GRANT ALL ON public.api_keys TO service_role, anon;
GRANT ALL ON public.audit_logs TO service_role, anon;
```

`013_species_law.sql` revokes anon and enables RLS.
If 008 was already applied in production, run 013 immediately.
