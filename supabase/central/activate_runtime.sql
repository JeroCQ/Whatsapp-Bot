-- Atomic publication using existing server permissions. No new table grants,
-- role memberships, owners, RLS bypass or source-data changes are introduced.
BEGIN;
ALTER TABLE public.chatty_runtime_services ADD COLUMN IF NOT EXISTS reported_mode
  text NOT NULL DEFAULT '' CHECK (reported_mode IN ('','legacy','central'));

CREATE OR REPLACE FUNCTION public.chatty_activate_runtime(p_epoch text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE run_state text; details jsonb; changed integer;
BEGIN
  IF p_epoch !~ '^[A-Za-z0-9_-]{16,80}$' THEN
    RAISE EXCEPTION 'invalid_migration_epoch';
  END IF;
  SELECT state,progress INTO STRICT run_state,details
    FROM public.chatty_migration_runs WHERE epoch=p_epoch FOR UPDATE;
  IF run_state <> 'frozen' OR details->>'rows_installed' IS DISTINCT FROM 'true'
      OR details->>'barrier_verified' IS DISTINCT FROM 'true' THEN
    RAISE EXCEPTION 'migration_installation_not_verified';
  END IF;
  IF (SELECT count(*) FROM public.chatty_runtime_businesses) <> 4
      OR EXISTS(SELECT 1 FROM public.chatty_runtime_businesses
                WHERE business_id NOT IN ('briosos','tanaka','memos','velvet') OR mode <> 'legacy') THEN
    RAISE EXCEPTION 'migration_business_inventory_changed';
  END IF;
  IF (SELECT count(*) FROM public.chatty_runtime_services
      WHERE role IN ('commercial-api','pilot-api','commercial-worker','pilot-worker')
        AND mode='legacy' AND frozen AND epoch=p_epoch) <> 4
      OR EXISTS(SELECT 1 FROM public.chatty_runtime_services
                WHERE role IN ('commercial-worker','pilot-worker') AND paused_epoch<>p_epoch) THEN
    RAISE EXCEPTION 'migration_services_not_drained';
  END IF;
  UPDATE public.chatty_runtime_businesses SET mode='central', version=version+1,
    migration=migration || jsonb_build_object('status','ready','epoch',p_epoch,
      'public_url_map',CASE WHEN business_id IN ('memos','velvet') THEN
        jsonb_build_object(rtrim(source_configuration->>'SUPABASE_URL','/'),
                          'https://tbvcvqddpppqlwuehdaf.supabase.co') ELSE '{}'::jsonb END),
    updated_at=now();
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>4 THEN RAISE EXCEPTION 'migration_business_count_changed'; END IF;
  UPDATE public.chatty_runtime_services SET mode='central', reported_mode='', paused_epoch=''
    WHERE role IN ('commercial-api','pilot-api','pilot-worker');
  UPDATE public.chatty_migration_runs SET progress=details ||
    '{"central_authority_published":true}'::jsonb WHERE epoch=p_epoch;
  RETURN jsonb_build_object('authority','central','queues_released',false,'restart_required',true);
END $$;
REVOKE ALL ON FUNCTION public.chatty_activate_runtime(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.chatty_activate_runtime(text) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
