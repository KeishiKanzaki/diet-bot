-- READ ONLY: run before 001_beta.sql. No user rows or credentials are returned.
-- One SELECT, one result cell: copy the full preflight_report back for review.
with columns_report as (
  select table_name, column_name, data_type, udt_name, is_nullable,
         column_default, is_identity, ordinal_position
  from information_schema.columns
  where table_schema = 'public' and table_name in ('users', 'food_logs', 'ai_usage')
), indexes_report as (
  select tablename, indexname, indexdef
  from pg_indexes
  where schemaname = 'public' and tablename in ('users', 'food_logs', 'ai_usage')
), constraints_report as (
  select c.relname as table_name, con.conname as constraint_name,
         pg_get_constraintdef(con.oid) as definition
  from pg_constraint con
  join pg_class c on c.oid = con.conrelid
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname in ('users', 'food_logs', 'ai_usage')
), policies_report as (
  select tablename, policyname, roles, cmd, permissive, qual, with_check
  from pg_policies
  where schemaname = 'public' and tablename in ('users', 'food_logs', 'ai_usage')
), rls_report as (
  select c.relname as table_name, c.relrowsecurity as rls_enabled,
         c.relforcerowsecurity as rls_forced
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname in ('users', 'food_logs', 'ai_usage')
    and c.relkind in ('r', 'p')
), grants_report as (
  select table_name, grantee, privilege_type
  from information_schema.role_table_grants
  where table_schema = 'public' and table_name in ('users', 'food_logs', 'ai_usage')
    and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')
)
select jsonb_pretty(jsonb_build_object(
  'columns', (select coalesce(jsonb_agg(to_jsonb(r) order by table_name, ordinal_position), '[]'::jsonb) from columns_report r),
  'indexes', (select coalesce(jsonb_agg(to_jsonb(r) order by tablename, indexname), '[]'::jsonb) from indexes_report r),
  'constraints', (select coalesce(jsonb_agg(to_jsonb(r) order by table_name, constraint_name), '[]'::jsonb) from constraints_report r),
  'rls', (select coalesce(jsonb_agg(to_jsonb(r) order by table_name), '[]'::jsonb) from rls_report r),
  'policies', (select coalesce(jsonb_agg(to_jsonb(r) order by tablename, policyname), '[]'::jsonb) from policies_report r),
  'grants', (select coalesce(jsonb_agg(to_jsonb(r) order by table_name, grantee, privilege_type), '[]'::jsonb) from grants_report r)
)) as preflight_report;
