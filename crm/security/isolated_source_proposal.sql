-- REVIEW PROPOSAL / SYNTHETIC DATABASES ONLY. NOT A BOT OR SUPABASE MIGRATION.
-- Apply as the owner/admin of a NEW isolated disposable source facade after
-- loading its public tables. Never run this against an existing bot database.
-- Prerequisites:
--   SET crm.reader_role = 'crm_staging_reader_<unique_suffix>';
--   public.crm_synthetic_source_marker(marker text) contains exactly the marker
--   'crm-synthetic-only-v1'. Database name: crm_test_source_* / crm_staging_source_*.
-- The loader sets the generated login password separately; no password belongs here.
DO $proposal$
DECLARE
    reader_name text := current_setting('crm.reader_role', true);
    database_name text := current_database();
    reader_oid oid;
    routine record;
    relation record;
    column_list text;
    expected_columns jsonb := '{
      "customers":["phone_number","name","created_at"],
      "conversation_states":["phone_number","current_state","is_paused","handoff_reason","last_interaction","chatwoot_conversation_id","customer_data","order_summary"],
      "message_logs":["id","phone_number","role","content","created_at","metadata","event_key"],
      "handoff_deliveries":["business_id","phone_number","request_id","status","attempts","retry_at","conversation_id","last_error","created_at","updated_at"]
    }'::jsonb;
BEGIN
    IF database_name NOT LIKE 'crm\_test\_source\_%' ESCAPE '\'
       AND database_name NOT LIKE 'crm\_staging\_source\_%' ESCAPE '\' THEN
        RAISE EXCEPTION 'Proposal refuses a database outside the synthetic source namespace';
    END IF;
    IF reader_name IS NULL OR reader_name !~ '^crm_staging_reader_[a-z0-9_]{1,40}$' THEN
        RAISE EXCEPTION 'Set a unique synthetic reader role in crm.reader_role';
    END IF;
    IF to_regclass('public.crm_synthetic_source_marker') IS NULL THEN
        RAISE EXCEPTION 'Synthetic source marker is required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.crm_synthetic_source_marker
                   WHERE marker = 'crm-synthetic-only-v1') THEN
        RAISE EXCEPTION 'Synthetic source marker is invalid';
    END IF;
    IF to_regclass('public.message_logs') IS NULL THEN
        RAISE EXCEPTION 'Synthetic message_logs table must be created first';
    END IF;
    SELECT oid INTO reader_oid FROM pg_roles WHERE rolname = reader_name;
    IF reader_oid IS NOT NULL THEN
        IF obj_description(reader_oid, 'pg_authid') IS DISTINCT FROM
           'CRM_SYNTHETIC_READER_v1:' || database_name THEN
            RAISE EXCEPTION 'Refusing to modify a role not owned by this synthetic fixture';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = reader_oid AND
                   (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit))
           OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = reader_oid) THEN
            RAISE EXCEPTION 'Existing synthetic role has unsafe flags or memberships';
        END IF;
    ELSE
        EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1', reader_name);
        EXECUTE format('COMMENT ON ROLE %I IS %L', reader_name,
                       'CRM_SYNTHETIC_READER_v1:' || database_name);
    END IF;

    -- PUBLIC privileges cannot be denied to just one login. These shared ACL
    -- changes are acceptable ONLY in this new isolated synthetic database.
    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC', database_name);
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM %I', database_name, reader_name);
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', database_name, reader_name);
    FOR relation IN SELECT nspname FROM pg_namespace
                    WHERE nspname NOT LIKE 'pg\_%' ESCAPE '\' AND nspname <> 'information_schema' LOOP
        EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', relation.nspname);
        EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', relation.nspname, reader_name);
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', relation.nspname, reader_name);
    END LOOP;
    -- Reject/strip callable side-effect paths, including PUBLIC SECURITY DEFINER
    -- functions, extension functions and volatile builtins such as pg_notify.
    FOR routine IN SELECT p.oid, n.nspname, p.proname, p.prokind,
                          pg_get_function_identity_arguments(p.oid) AS arguments
                   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE p.provolatile = 'v' OR n.nspname <> 'pg_catalog' OR p.oid >= 16384 LOOP
        EXECUTE format('REVOKE EXECUTE ON %s %I.%I(%s) FROM PUBLIC, %I',
                       CASE WHEN routine.prokind = 'a' THEN 'FUNCTION' ELSE 'ROUTINE' END,
                       routine.nspname, routine.proname, routine.arguments, reader_name);
    END LOOP;
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', reader_name);
    FOR relation IN SELECT c.oid, c.relname, c.relkind, c.relowner
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND expected_columns ? c.relname LOOP
        IF relation.relkind <> 'r' OR EXISTS
           (SELECT 1 FROM pg_inherits WHERE inhrelid = relation.oid OR inhparent = relation.oid) THEN
            RAISE EXCEPTION 'Only standalone base tables are allowed in the synthetic facade';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
                   JOIN pg_namespace n ON n.oid = t.typnamespace
                   WHERE a.attrelid = relation.oid AND a.attnum > 0 AND NOT a.attisdropped
                     AND expected_columns -> relation.relname ? a.attname
                     AND (n.nspname <> 'pg_catalog' OR t.typtype <> 'b' OR t.typname NOT IN
                       ('text','varchar','bpchar','bool','int2','int4','int8','timestamptz','timestamp',
                        'date','json','jsonb','numeric','float4','float8','uuid'))) THEN
            RAISE EXCEPTION 'Custom/domain/array types are not supported in the synthetic facade';
        END IF;
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, %I', relation.relname, reader_name);
        SELECT string_agg(format('%I', a.attname), ', ' ORDER BY a.attnum) INTO column_list
        FROM pg_attribute a WHERE a.attrelid = relation.oid AND a.attnum > 0 AND NOT a.attisdropped
          AND expected_columns -> relation.relname ? a.attname;
        IF column_list IS NULL THEN
            RAISE EXCEPTION 'Synthetic source table has no supported columns';
        END IF;
        EXECUTE format('GRANT SELECT (%s) ON TABLE public.%I TO %I', column_list, relation.relname, reader_name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', relation.relname);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', relation.relname);
        EXECUTE format('DROP POLICY IF EXISTS crm_synthetic_reader_select ON public.%I', relation.relname);
        EXECUTE format('CREATE POLICY crm_synthetic_reader_select ON public.%I FOR SELECT TO %I USING (true)',
                       relation.relname, reader_name);
    END LOOP;
END
$proposal$;
