-- Apply only after the registry and chatty_runtime login exist.
-- These are new destination tables, never the active unprefixed Tanaka tables.
-- Temporary DDL membership/schema privileges are restored before commit.
BEGIN;
GRANT chatty_runtime TO postgres WITH INHERIT FALSE, SET TRUE;
GRANT CREATE ON SCHEMA public TO chatty_runtime;
DO $$
DECLARE business text; suffix text; relation text; sequence record;
BEGIN
  IF EXISTS(SELECT 1 FROM public.chatty_runtime_businesses WHERE mode <> 'legacy') THEN
    RAISE EXCEPTION 'Run destination preparation before central activation';
  END IF;
  FOREACH business IN ARRAY ARRAY['briosos','memos','velvet'] LOOP
    FOREACH suffix IN ARRAY ARRAY['customers','dashboard_admins','catalog_assets',
      'conversation_states','processed_webhook_events','message_logs',
      'handoff_deliveries','crm_control_commands','crm_send_attempts','crm_media_assets'] LOOP
      relation := 'chatty_' || business || '_' || suffix;
      IF to_regclass('public.' || relation) IS NOT NULL THEN
        IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('public.' || relation)) THEN
          RAISE EXCEPTION 'Destination RLS must remain enabled';
        END IF;
        EXECUTE format('ALTER TABLE public.%I OWNER TO chatty_runtime', relation);
        EXECUTE format('GRANT ALL ON public.%I TO postgres', relation);
      END IF;
    END LOOP;
  END LOOP;
  FOREACH relation IN ARRAY ARRAY['chatty_migration_runs','chatty_runtime_tokens'] LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO chatty_runtime', relation);
    EXECUTE format('GRANT ALL ON public.%I TO postgres', relation);
  END LOOP;
  FOR sequence IN SELECT sequencename FROM pg_sequences WHERE schemaname='public'
    AND (sequencename LIKE 'chatty_briosos_%' OR sequencename LIKE 'chatty_memos_%' OR sequencename LIKE 'chatty_velvet_%') LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO chatty_runtime',sequence.sequencename);
  END LOOP;
END $$;
REVOKE CREATE ON SCHEMA public FROM chatty_runtime;
GRANT chatty_runtime TO postgres WITH INHERIT FALSE, SET FALSE;
COMMIT;
