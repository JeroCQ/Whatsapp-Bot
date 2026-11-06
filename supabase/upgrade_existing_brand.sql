-- Idempotent upgrade for an EXISTING bot Supabase (Tanaka, Memo's, Velvet, or
-- another BUSINESS_ID). Back up the project first. Do not use this to initialize
-- an empty project; new projects must run supabase/bootstrap.sql instead.

-- PRECHECK: this must return zero rows before continuing. Resolve stale duplicate
-- Chatwoot IDs first or the unique index below cannot be created.
SELECT chatwoot_conversation_id, count(*) AS duplicate_count
FROM public.conversation_states
WHERE chatwoot_conversation_id IS NOT NULL
GROUP BY chatwoot_conversation_id
HAVING count(*) > 1;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.conversation_states
    WHERE chatwoot_conversation_id IS NOT NULL
    GROUP BY chatwoot_conversation_id
    HAVING count(*) > 1
  ) THEN
    RAISE EXCEPTION 'Duplicate chatwoot_conversation_id values must be resolved before upgrading';
  END IF;
END $$;

-- Keep the legacy column name for runtime/dashboard compatibility. Its value is
-- the WhatsApp recipient identity, which can be a phone number or a longer
-- business-scoped user ID. PostgreSQL preserves the existing values and all
-- primary/unique/foreign keys when widening these compatible string types.
-- One DO statement makes the three-column change atomic and safe to rerun.
DO $$ BEGIN
  ALTER TABLE public.customers ALTER COLUMN phone_number TYPE text;
  ALTER TABLE public.conversation_states ALTER COLUMN phone_number TYPE text;
  ALTER TABLE public.message_logs ALTER COLUMN phone_number TYPE text;
END $$;

-- Current conversation, Chatwoot, follow-up, and durable-memory fields.
ALTER TABLE public.conversation_states
  ADD COLUMN IF NOT EXISTS chatwoot_conversation_id integer,
  ADD COLUMN IF NOT EXISTS follow_up_token varchar,
  ADD COLUMN IF NOT EXISTS customer_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS order_summary text;

-- Queue/webhook idempotency used by every deployment.
CREATE TABLE IF NOT EXISTS public.processed_webhook_events (
  source varchar NOT NULL,
  event_id varchar NOT NULL,
  phone_number varchar,
  status varchar NOT NULL DEFAULT 'received',
  received_at timestamptz DEFAULT now(),
  processed_at timestamptz,
  error text,
  PRIMARY KEY (source, event_id)
);
ALTER TABLE public.processed_webhook_events
  ADD COLUMN IF NOT EXISTS phone_number varchar,
  ADD COLUMN IF NOT EXISTS status varchar NOT NULL DEFAULT 'received',
  ADD COLUMN IF NOT EXISTS received_at timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS processed_at timestamptz,
  ADD COLUMN IF NOT EXISTS error text;

-- Delivery markers use `system`; human Chatwoot history uses `asesor`.
ALTER TABLE public.message_logs DROP CONSTRAINT IF EXISTS message_logs_role_check;
ALTER TABLE public.message_logs
  ADD CONSTRAINT message_logs_role_check
  CHECK (role IN ('user', 'model', 'system', 'asesor'));

-- Correlated message metrics and durable, bounded human-handoff delivery.
ALTER TABLE public.conversation_states
  ADD COLUMN IF NOT EXISTS follow_up_not_before timestamptz,
  ADD COLUMN IF NOT EXISTS follow_up_suppressed boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS follow_up_suppression_reason text;
ALTER TABLE public.message_logs
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS event_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_logs_event_key ON public.message_logs(event_key);
CREATE TABLE IF NOT EXISTS public.handoff_deliveries (
  business_id text NOT NULL,
  phone_number text NOT NULL REFERENCES public.customers(phone_number),
  request_id text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'reserved', 'sending', 'created', 'unknown', 'exhausted', 'cancelled')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  retry_at timestamptz NOT NULL DEFAULT now(),
  conversation_id bigint,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, phone_number)
);
CREATE INDEX IF NOT EXISTS idx_handoff_deliveries_pending
  ON public.handoff_deliveries(business_id, status, retry_at);
ALTER TABLE public.handoff_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.handoff_deliveries FROM anon, authenticated;

CREATE INDEX IF NOT EXISTS idx_message_logs_phone_created_at
  ON public.message_logs(phone_number, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_states_chatwoot_conversation_id
  ON public.conversation_states(chatwoot_conversation_id)
  WHERE chatwoot_conversation_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_states_active_chatwoot_conversation_id
  ON public.conversation_states(chatwoot_conversation_id)
  WHERE chatwoot_conversation_id IS NOT NULL;

-- Secured dashboard allow-list. Password-proxy-only deployments may leave it empty.
CREATE TABLE IF NOT EXISTS public.dashboard_admins (
  user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.dashboard_admins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.dashboard_admins FROM anon, authenticated;

INSERT INTO storage.buckets AS catalog_bucket (id, name, public, file_size_limit)
VALUES ('catalogos', 'catalogos', true, 209715200)
ON CONFLICT (id) DO UPDATE SET
  public = true,
  file_size_limit = GREATEST(COALESCE(catalog_bucket.file_size_limit, 0), 209715200);

CREATE TABLE IF NOT EXISTS public.catalog_assets (
  business_id text NOT NULL,
  catalog_id text NOT NULL CHECK (catalog_id ~ '^catalogo_[a-z0-9_]{1,52}$'),
  public_name text NOT NULL CHECK (length(trim(public_name)) BETWEEN 1 AND 120),
  description text NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 2000),
  media_type text NOT NULL DEFAULT 'document' CHECK (media_type IN ('document', 'image')),
  filename text,
  content_type text CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')),
  size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, catalog_id)
);
ALTER TABLE public.catalog_assets
  ADD COLUMN IF NOT EXISTS content_type text,
  ADD COLUMN IF NOT EXISTS size_bytes bigint;
ALTER TABLE public.catalog_assets
  DROP CONSTRAINT IF EXISTS catalog_assets_description_check;
ALTER TABLE public.catalog_assets
  ADD CONSTRAINT catalog_assets_description_check
  CHECK (length(trim(description)) BETWEEN 1 AND 2000);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'catalog_assets_content_type_check') THEN
    ALTER TABLE public.catalog_assets ADD CONSTRAINT catalog_assets_content_type_check
      CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/webp'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'catalog_assets_size_bytes_check') THEN
    ALTER TABLE public.catalog_assets ADD CONSTRAINT catalog_assets_size_bytes_check
      CHECK (size_bytes IS NULL OR size_bytes >= 0);
  END IF;
END $$;
ALTER TABLE public.catalog_assets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.catalog_assets FROM anon, authenticated;

-- Every result must be true before deploying the new bot commit.
SELECT
  to_regclass('public.customers') IS NOT NULL AS has_customers,
  to_regclass('public.conversation_states') IS NOT NULL AS has_conversation_states,
  to_regclass('public.message_logs') IS NOT NULL AS has_message_logs,
  to_regclass('public.processed_webhook_events') IS NOT NULL AS has_webhook_events,
  to_regclass('public.dashboard_admins') IS NOT NULL AS has_dashboard_admins,
  to_regclass('public.catalog_assets') IS NOT NULL AS has_catalog_assets,
  (
    SELECT count(*) = 3 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ('customers', 'conversation_states', 'message_logs')
      AND column_name = 'phone_number' AND data_type = 'text'
  ) AS has_unbounded_whatsapp_identities,
  EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'conversation_states'
      AND column_name = 'follow_up_token'
  ) AS has_follow_up_token,
  EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'conversation_states'
      AND column_name = 'customer_data'
  ) AS has_customer_data,
  EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'conversation_states'
      AND column_name = 'order_summary'
  ) AS has_order_summary,
  to_regclass('public.uq_conversation_states_active_chatwoot_conversation_id') IS NOT NULL
    AS has_unique_chatwoot_index,
  EXISTS (
    SELECT 1 FROM storage.buckets WHERE id = 'catalogos' AND public
  ) AS has_public_catalog_bucket;

-- CRM CONTROL BEGIN
-- Bot-owned authority. Apply only to an isolated pilot after review/backup.
ALTER TABLE public.conversation_states
  ADD COLUMN IF NOT EXISTS crm_control_version bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS crm_control_phase text NOT NULL DEFAULT 'ai',
  ADD COLUMN IF NOT EXISTS crm_control_owner text,
  ADD COLUMN IF NOT EXISTS crm_control_business_id text;

CREATE TABLE IF NOT EXISTS public.crm_control_commands (
  business_id text NOT NULL,
  command_id text NOT NULL,
  phone_number text NOT NULL REFERENCES public.customers(phone_number),
  operation text NOT NULL,
  request jsonb NOT NULL,
  response jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, command_id)
);
CREATE TABLE IF NOT EXISTS public.crm_send_attempts (
  business_id text NOT NULL,
  attempt_id text NOT NULL,
  phone_number text NOT NULL REFERENCES public.customers(phone_number),
  kind text NOT NULL CHECK (kind IN ('auto','human')),
  actor text NOT NULL DEFAULT '',
  version bigint NOT NULL,
  request jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('sending','accepted','delivered','read','failed','unknown')),
  provider_message_id text,
  detail text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, attempt_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS crm_send_attempt_provider_id
  ON public.crm_send_attempts(business_id, provider_message_id)
  WHERE provider_message_id IS NOT NULL AND provider_message_id <> '';
CREATE INDEX IF NOT EXISTS crm_send_attempt_inflight
  ON public.crm_send_attempts(business_id, phone_number, kind, status);
ALTER TABLE public.crm_control_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_send_attempts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.crm_control_commands, public.crm_send_attempts FROM PUBLIC;
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
    IF to_regrole(r) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON public.crm_control_commands, public.crm_send_attempts FROM %I',r);
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.crm_control_view(p_business_id text, p_phone text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.conversation_states%ROWTYPE; inbound public.message_logs%ROWTYPE;
        received timestamptz; expiry timestamptz; phase text; inbound_ids jsonb;
BEGIN
  SELECT * INTO s FROM public.conversation_states WHERE phone_number=p_phone;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','conversation_not_found','http_status',404); END IF;
  IF s.crm_control_business_id IS NOT NULL AND s.crm_control_business_id <> p_business_id THEN
    RETURN jsonb_build_object('error','business_mismatch','http_status',403);
  END IF;
  phase := CASE WHEN s.is_paused AND s.crm_control_phase='ai' THEN 'waiting' ELSE s.crm_control_phase END;
  SELECT * INTO inbound FROM public.message_logs WHERE phone_number=p_phone AND role='user'
    ORDER BY created_at DESC,id DESC LIMIT 1;
  BEGIN received := NULLIF(inbound.metadata->>'received_at','')::timestamptz;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN received := NULL; END;
  IF received IS NOT NULL AND received <= now()+interval '1 minute' THEN expiry := received+interval '24 hours'; END IF;
  -- A human reply covers the inbound set visible at reservation, never later arrivals.
  SELECT COALESCE(jsonb_agg(m.metadata->>'source_event_id' ORDER BY m.created_at,m.id),'[]'::jsonb)
    INTO inbound_ids FROM public.message_logs m
    WHERE m.phone_number=p_phone AND m.role='user' AND m.metadata->>'source_event_id' IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.message_logs r WHERE r.phone_number=p_phone
        AND r.role='system' AND r.content LIKE 'RESOLVED:%' AND (r.created_at,r.id)>=(m.created_at,m.id));
  RETURN jsonb_build_object('business_id',p_business_id,'phone_number',p_phone,'provider','crm',
    'phase',phase,'version',s.crm_control_version,'owner',s.crm_control_owner,
    'is_paused',s.is_paused,'confirmed',phase='human' AND s.is_paused,
    'can_send',COALESCE(phase='human' AND s.is_paused AND s.crm_control_owner IS NOT NULL AND expiry>now()
      AND NOT EXISTS (SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id
        AND phone_number=p_phone AND status IN ('sending','unknown')),false),
    'window_expires_at',expiry,'inbound_message_ids',inbound_ids);
END $$;

CREATE OR REPLACE FUNCTION public.crm_control_dispatch(
  p_business_id text,p_phone text,p_action text,p_request_id text DEFAULT '',
  p_actor text DEFAULT '',p_expected_version bigint DEFAULT NULL,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.conversation_states%ROWTYPE; cmd public.crm_control_commands%ROWTYPE;
        attempt public.crm_send_attempts%ROWTYPE; wanted jsonb; v_response jsonb;
        control jsonb; target_status text; provider_id text; blocked boolean;
BEGIN
  IF p_business_id IS NULL OR p_business_id !~ '^[a-z][a-z0-9_-]{0,62}$' THEN
    RETURN jsonb_build_object('error','invalid_business','http_status',400);
  END IF;
  IF p_action IN ('finish_send','receipt') THEN
    IF p_request_id <> '' THEN
      SELECT * INTO attempt FROM public.crm_send_attempts
        WHERE business_id=p_business_id AND attempt_id=p_request_id;
    ELSE
      SELECT * INTO attempt FROM public.crm_send_attempts
        WHERE business_id=p_business_id AND provider_message_id=p_payload->>'provider_message_id';
    END IF;
    IF NOT FOUND THEN RETURN jsonb_build_object('error','send_attempt_not_found','http_status',404); END IF;
    IF p_phone IS NOT NULL AND p_phone <> attempt.phone_number THEN
      RETURN jsonb_build_object('error','command_identity_mismatch','http_status',409);
    END IF;
    p_phone := attempt.phone_number;
    p_request_id := attempt.attempt_id;
  END IF;
  SELECT * INTO s FROM public.conversation_states WHERE phone_number=p_phone FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('error','conversation_not_found','http_status',404); END IF;
  IF s.crm_control_business_id IS NOT NULL AND s.crm_control_business_id <> p_business_id THEN
    RETURN jsonb_build_object('error','business_mismatch','http_status',403);
  END IF;
  UPDATE public.conversation_states SET crm_control_business_id=p_business_id WHERE phone_number=p_phone;

  IF s.crm_control_phase='pausing' AND NOT EXISTS (
      SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
        AND kind='auto' AND status IN ('sending','unknown')) THEN
    UPDATE public.conversation_states SET crm_control_phase='human' WHERE phone_number=p_phone;
    s.crm_control_phase := 'human';
    UPDATE public.crm_control_commands SET response=response || jsonb_build_object(
      'status','confirmed','control',public.crm_control_view(p_business_id,p_phone)),updated_at=now()
      WHERE business_id=p_business_id AND phone_number=p_phone AND operation='take'
        AND request->>'actor'=s.crm_control_owner AND response->>'status'='pending';
  END IF;
  IF p_action='read' THEN RETURN public.crm_control_view(p_business_id,p_phone); END IF;
  IF p_action='command' THEN
    SELECT * INTO cmd FROM public.crm_control_commands WHERE business_id=p_business_id AND command_id=p_request_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('error','command_not_found','http_status',404); END IF;
    IF cmd.phone_number <> p_phone THEN RETURN jsonb_build_object('error','command_identity_mismatch','http_status',409); END IF;
    SELECT * INTO attempt FROM public.crm_send_attempts WHERE business_id=p_business_id AND attempt_id=p_request_id;
    v_response := cmd.response;
    IF FOUND THEN v_response := v_response || jsonb_build_object('status',attempt.status,'provider_message_id',attempt.provider_message_id); END IF;
    RETURN v_response || jsonb_build_object('control',public.crm_control_view(p_business_id,p_phone));
  END IF;

  IF p_action IN ('take','close','handoff','reset','transfer','begin_send') THEN
    IF p_request_id IS NULL OR length(p_request_id) NOT BETWEEN 1 AND 160 THEN
      RETURN jsonb_build_object('error','invalid_command_id','http_status',400);
    END IF;
    wanted := jsonb_build_object('operation',p_action,'phone',p_phone,'actor',p_actor,
                                'version',p_expected_version,'payload',p_payload);
    IF p_action='begin_send' THEN
      SELECT * INTO attempt FROM public.crm_send_attempts WHERE business_id=p_business_id AND attempt_id=p_request_id;
      IF FOUND THEN
        IF attempt.phone_number <> p_phone OR (attempt.request #- '{payload,inbound_message_ids}') <> (wanted #- '{payload,inbound_message_ids}') THEN
          RETURN jsonb_build_object('error','idempotency_conflict','http_status',409);
        END IF;
        RETURN jsonb_build_object('command_id',p_request_id,'status',attempt.status,
            'provider_message_id',attempt.provider_message_id,'control',public.crm_control_view(p_business_id,p_phone));
      END IF;
    ELSE
      SELECT * INTO cmd FROM public.crm_control_commands WHERE business_id=p_business_id AND command_id=p_request_id;
      IF FOUND THEN
        IF cmd.phone_number <> p_phone OR cmd.request <> wanted THEN
          RETURN jsonb_build_object('error','idempotency_conflict','http_status',409);
        END IF;
        v_response := cmd.response || jsonb_build_object('control',public.crm_control_view(p_business_id,p_phone));
        IF p_action='take' THEN v_response := v_response || jsonb_build_object('status',CASE WHEN s.crm_control_phase='human' THEN 'confirmed' ELSE 'pending' END); END IF;
        RETURN v_response;
      END IF;
    END IF;
  END IF;

  IF p_action='handoff' THEN
    IF s.crm_control_phase NOT IN ('human','pausing') THEN
      UPDATE public.conversation_states SET is_paused=true,current_state='HUMAN_HANDOFF',
        handoff_reason=left(COALESCE(p_payload->>'reason','Human attention required'),255),
        crm_control_phase='waiting',crm_control_owner=NULL,crm_control_version=crm_control_version+1,
        follow_up_token=NULL,follow_up_suppressed=true,follow_up_suppression_reason='crm_handoff'
        WHERE phone_number=p_phone;
      INSERT INTO public.message_logs(phone_number,role,content,metadata,event_key)
        VALUES(p_phone,'system','HANDOFF: '||COALESCE(p_payload->>'reason','Human attention required'),
          jsonb_build_object('business_id',p_business_id,'source','crm_handoff','source_event_id',p_request_id),
          'crm-handoff:'||p_business_id||':'||p_request_id) ON CONFLICT(event_key) DO NOTHING;
    END IF;
    v_response := jsonb_build_object('command_id',p_request_id,'status','confirmed','control',public.crm_control_view(p_business_id,p_phone));
  ELSIF p_action IN ('take','close','reset','transfer','begin_send') THEN
    IF p_expected_version IS NULL OR p_expected_version<>s.crm_control_version THEN
      RETURN jsonb_build_object('error','control_version_conflict','http_status',409);
    END IF;
    IF p_action='reset' THEN
      IF COALESCE(p_actor,'')<>'bot' OR COALESCE(s.is_paused,true) OR s.crm_control_phase<>'ai' THEN
        RETURN jsonb_build_object('error','active_ai_required_for_reset','http_status',409);
      END IF;
      IF EXISTS(SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
                  AND status IN ('sending','unknown')) THEN
        RETURN jsonb_build_object('error','unsettled_send_blocks_reset','http_status',409);
      END IF;
      UPDATE public.conversation_states SET current_state='GREETING',customer_data='{}'::jsonb,order_summary=NULL,
        handoff_reason=NULL,chatwoot_conversation_id=NULL,crm_control_owner=NULL,
        crm_control_version=crm_control_version+1,follow_up_token=NULL,follow_up_not_before=NULL
        WHERE phone_number=p_phone;
      -- Keep the CRM's incremental history/audit; reset only the bot's context.
      INSERT INTO public.message_logs(phone_number,role,content,metadata,event_key)
        VALUES(p_phone,'system','RESOLVED: Conversation reset; AI context cleared.',
          jsonb_build_object('business_id',p_business_id,'source','crm_reset','source_event_id',p_request_id,'actor','bot'),
          'crm-reset:'||p_business_id||':'||p_request_id) ON CONFLICT(event_key) DO NOTHING;
      v_response := jsonb_build_object('command_id',p_request_id,'status','confirmed','control',public.crm_control_view(p_business_id,p_phone));
    ELSIF p_action='transfer' THEN
      IF length(trim(p_actor)) NOT BETWEEN 1 AND 200
         OR length(trim(COALESCE(p_payload->>'target_actor',''))) NOT BETWEEN 1 AND 200
         OR NOT s.is_paused
         OR NOT ((s.crm_control_phase='human' AND s.crm_control_owner IS NOT DISTINCT FROM p_actor)
              OR (s.crm_control_phase IN ('ai','waiting') AND s.crm_control_owner IS NULL)) THEN
        RETURN jsonb_build_object('error','confirmed_owner_or_unassigned_waiting_required','http_status',409);
      END IF;
      IF EXISTS(SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
                  AND status IN ('sending','unknown')) THEN
        RETURN jsonb_build_object('error','unsettled_send_blocks_transfer','http_status',409);
      END IF;
      UPDATE public.conversation_states SET crm_control_phase='human',crm_control_owner=p_payload->>'target_actor',
        crm_control_version=crm_control_version+1 WHERE phone_number=p_phone;
      INSERT INTO public.message_logs(phone_number,role,content,metadata,event_key)
        VALUES(p_phone,'system','CONTROL: Atención transferida a otro asesor.',
          jsonb_build_object('business_id',p_business_id,'source','crm_transfer','source_event_id',p_request_id,
                             'actor',p_actor,'target_actor',p_payload->>'target_actor'),
          'crm-transfer:'||p_business_id||':'||p_request_id) ON CONFLICT(event_key) DO NOTHING;
      v_response := jsonb_build_object('command_id',p_request_id,'status','confirmed','control',public.crm_control_view(p_business_id,p_phone));
    ELSIF p_action='take' THEN
      IF length(trim(p_actor)) NOT BETWEEN 1 AND 200 THEN RETURN jsonb_build_object('error','actor_required','http_status',400); END IF;
      IF s.crm_control_phase IN ('human','pausing') AND s.crm_control_owner IS DISTINCT FROM p_actor THEN
        RETURN jsonb_build_object('error','controlled_by_another_actor','http_status',409);
      END IF;
      SELECT EXISTS(SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
        AND kind='auto' AND status IN ('sending','unknown')) INTO blocked;
      UPDATE public.conversation_states SET is_paused=true,current_state='HUMAN_HANDOFF',
        handoff_reason=left(COALESCE(p_payload->>'reason','CRM human takeover'),255),
        crm_control_phase=CASE WHEN blocked THEN 'pausing' ELSE 'human' END,crm_control_owner=p_actor,
        crm_control_version=crm_control_version+1,follow_up_token=NULL,follow_up_suppressed=true,
        follow_up_suppression_reason='crm_human_control' WHERE phone_number=p_phone;
      v_response := jsonb_build_object('command_id',p_request_id,'status',CASE WHEN blocked THEN 'pending' ELSE 'confirmed' END,
                                     'control',public.crm_control_view(p_business_id,p_phone));
    ELSIF p_action='close' THEN
      IF length(trim(p_actor)) NOT BETWEEN 1 AND 200 OR NOT s.is_paused OR NOT (
        (s.crm_control_phase='human' AND s.crm_control_owner IS NOT DISTINCT FROM p_actor)
        OR (s.crm_control_phase IN ('ai','waiting') AND s.crm_control_owner IS NULL)
      ) THEN
        RETURN jsonb_build_object('error','confirmed_owner_required','http_status',409);
      END IF;
      IF EXISTS(SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
                  AND status IN ('sending','unknown')) THEN
        RETURN jsonb_build_object('error','unsettled_send_blocks_close','http_status',409);
      END IF;
      UPDATE public.conversation_states SET is_paused=false,current_state='GREETING',handoff_reason=NULL,
        crm_control_phase='ai',crm_control_owner=NULL,crm_control_version=crm_control_version+1,
        follow_up_token=NULL,follow_up_suppressed=false,follow_up_suppression_reason=NULL
        WHERE phone_number=p_phone;
      INSERT INTO public.message_logs(phone_number,role,content,metadata,event_key)
        VALUES(p_phone,'system','RESOLVED: CRM case closed; AI control restored.',
          jsonb_build_object('business_id',p_business_id,'source','crm_close','source_event_id',p_request_id,'actor',p_actor),
          'crm-close:'||p_business_id||':'||p_request_id) ON CONFLICT(event_key) DO NOTHING;
      v_response := jsonb_build_object('command_id',p_request_id,'status','confirmed','control',public.crm_control_view(p_business_id,p_phone));
    ELSE
      control := public.crm_control_view(p_business_id,p_phone);
      IF p_payload->>'kind' NOT IN ('auto','human') THEN RETURN jsonb_build_object('error','invalid_send_kind','http_status',400); END IF;
      IF p_payload->>'kind'='auto' AND p_payload->>'purpose'='handoff_ack' THEN
        -- One text acknowledgement of this exact bot-created handoff. This is
        -- still an auto attempt, so takeover waits for its sending/unknown state.
        IF COALESCE(p_actor,'')<>'bot' OR NOT COALESCE(s.is_paused,false) OR s.crm_control_phase<>'waiting' OR s.crm_control_owner IS NOT NULL
           OR length(COALESCE(p_payload->>'text','')) NOT BETWEEN 1 AND 4096 OR p_payload ? 'attachments' THEN
          RETURN jsonb_build_object('error','automatic_send_fenced','http_status',409);
        END IF;
        SELECT * INTO cmd FROM public.crm_control_commands WHERE business_id=p_business_id
          AND phone_number=p_phone AND command_id=p_payload->>'handoff_request_id' AND operation='handoff';
        IF NOT FOUND OR NOT COALESCE((cmd.response#>>'{control,version}')::bigint=s.crm_control_version,false) THEN
          RETURN jsonb_build_object('error','handoff_notice_not_authorized','http_status',409);
        END IF;
        IF EXISTS(SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
            AND request#>>'{payload,purpose}'='handoff_ack'
            AND request#>>'{payload,handoff_request_id}'=p_payload->>'handoff_request_id') THEN
          RETURN jsonb_build_object('error','handoff_notice_already_reserved','http_status',409);
        END IF;
      ELSIF p_payload->>'kind'='auto' AND (s.is_paused OR s.crm_control_phase<>'ai') THEN
        RETURN jsonb_build_object('error','automatic_send_fenced','http_status',409);
      END IF;
      IF p_payload->>'kind'='human' THEN
        IF s.crm_control_phase<>'human' OR NOT s.is_paused OR s.crm_control_owner IS DISTINCT FROM p_actor THEN
          RETURN jsonb_build_object('error','confirmed_owner_required','http_status',409);
        END IF;
        IF NOT COALESCE((control->>'can_send')::boolean,false) THEN
          RETURN jsonb_build_object('error','whatsapp_window_closed_or_unverified','http_status',409);
        END IF;
        IF EXISTS(SELECT 1 FROM public.crm_send_attempts WHERE business_id=p_business_id AND phone_number=p_phone
                    AND kind='human' AND status IN ('sending','unknown')) THEN
          RETURN jsonb_build_object('error','unsettled_send_blocks_human_reply','http_status',409);
        END IF;
        UPDATE public.conversation_states SET crm_control_version=crm_control_version+1 WHERE phone_number=p_phone;
        control := public.crm_control_view(p_business_id,p_phone);
      END IF;
      INSERT INTO public.crm_send_attempts(business_id,attempt_id,phone_number,kind,actor,version,request,status)
        VALUES(p_business_id,p_request_id,p_phone,p_payload->>'kind',p_actor,p_expected_version,wanted,'sending');
      v_response := jsonb_build_object('command_id',p_request_id,'status','reserved','control',control);
    END IF;
  ELSIF p_action IN ('finish_send','receipt') THEN
    SELECT * INTO attempt FROM public.crm_send_attempts WHERE business_id=p_business_id AND attempt_id=p_request_id FOR UPDATE;
    target_status := p_payload->>'status';
    provider_id := NULLIF(p_payload->>'provider_message_id','');
    IF p_action='receipt' AND target_status NOT IN ('accepted','delivered','read','failed') THEN
      RETURN jsonb_build_object('error','invalid_receipt_status','http_status',400);
    END IF;
    IF p_action='finish_send' AND target_status NOT IN ('accepted','failed','unknown') THEN
      RETURN jsonb_build_object('error','invalid_send_status','http_status',400);
    END IF;
    IF attempt.provider_message_id IS NOT NULL AND provider_id IS NOT NULL AND attempt.provider_message_id<>provider_id THEN
      RETURN jsonb_build_object('error','provider_identity_conflict','http_status',409);
    END IF;
    -- Do not regress delivery receipts when the HTTP acknowledgement arrives late.
    IF attempt.status='read' OR (attempt.status='delivered' AND target_status<>'read') THEN
      target_status := attempt.status;
    ELSIF attempt.status='failed' AND target_status NOT IN ('delivered','read') THEN
      -- A late HTTP acknowledgement or Meta sent callback cannot erase a known failure.
      target_status := 'failed';
    ELSIF attempt.status='accepted' AND target_status='unknown' THEN
      target_status := 'accepted';
    END IF;
    UPDATE public.crm_send_attempts SET status=target_status,
      provider_message_id=COALESCE(provider_id,provider_message_id),detail=left(p_payload->>'detail',100),updated_at=now()
      WHERE business_id=p_business_id AND attempt_id=p_request_id;
    IF attempt.kind='human' AND target_status IN ('accepted','delivered','read','failed','unknown') THEN
      INSERT INTO public.message_logs(phone_number,role,content,metadata,event_key)
        VALUES(p_phone,'asesor',COALESCE(attempt.request->'payload'->>'text',''),
          jsonb_build_object('business_id',p_business_id,'source','human','source_event_id',p_request_id,
            'actor',attempt.actor,'command_id',p_request_id,
            'attachments',COALESCE(attempt.request->'payload'->'attachments','[]'::jsonb),
            'provider_message_id',COALESCE(provider_id,attempt.provider_message_id),'delivery_status',target_status,
            'inbound_message_ids',COALESCE(attempt.request->'payload'->'inbound_message_ids','[]'::jsonb)),
          'crm-human:'||p_business_id||':'||p_request_id) ON CONFLICT(event_key) DO NOTHING;
    END IF;
    IF attempt.kind='human' THEN
      UPDATE public.message_logs SET metadata=COALESCE(metadata,'{}'::jsonb) || jsonb_build_object(
        'delivery_status',target_status,'provider_message_id',COALESCE(provider_id,attempt.provider_message_id))
        WHERE event_key='crm-human:'||p_business_id||':'||p_request_id;
      IF attempt.status IS DISTINCT FROM target_status AND target_status IN ('accepted','delivered','read','failed') THEN
        -- Metadata edits alone are invisible to the incremental ID watermark. Append a
        -- receipt fact, without message content, so the next bounded poll sees it.
        INSERT INTO public.message_logs(phone_number,role,content,metadata,event_key)
          VALUES(p_phone,'system','DELIVERY_STATUS: '||target_status,
            jsonb_build_object('business_id',p_business_id,'source','crm_delivery',
              'command_id',p_request_id,'source_event_id',p_request_id,'delivery_status',target_status,
              'provider_message_id',COALESCE(provider_id,attempt.provider_message_id)),
            'crm-delivery:'||p_business_id||':'||p_request_id||':'||target_status)
          ON CONFLICT(event_key) DO NOTHING;
      END IF;
    END IF;
    v_response := jsonb_build_object('command_id',p_request_id,'status',target_status,
         'provider_message_id',COALESCE(provider_id,attempt.provider_message_id),
         'control',public.crm_control_view(p_business_id,p_phone));
    UPDATE public.crm_control_commands SET response=v_response,updated_at=now()
      WHERE business_id=p_business_id AND command_id=p_request_id;
    RETURN v_response;
  ELSE
    RETURN jsonb_build_object('error','unsupported_control_action','http_status',400);
  END IF;
  INSERT INTO public.crm_control_commands(business_id,command_id,phone_number,operation,request,response)
    VALUES(p_business_id,p_request_id,p_phone,p_action,wanted,v_response);
  RETURN v_response;
EXCEPTION WHEN unique_violation THEN
  RETURN jsonb_build_object('error','idempotency_conflict','http_status',409);
END $$;
REVOKE ALL ON FUNCTION public.crm_control_view(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb) FROM PUBLIC;
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
    IF to_regrole(r) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON FUNCTION public.crm_control_view(text,text) FROM %I',r);
      EXECUTE format('REVOKE ALL ON FUNCTION public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb) FROM %I',r);
    END IF;
  END LOOP;
END $$;
DO $$ BEGIN
  IF to_regrole('service_role') IS NOT NULL THEN
    GRANT EXECUTE ON FUNCTION public.crm_control_view(text,text) TO service_role;
    GRANT EXECUTE ON FUNCTION public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb) TO service_role;
  END IF;
END $$;
-- CRM CONTROL END

-- CRM PRIVATE MEDIA BEGIN
-- Optional private storage: application defaults remain disabled. Run separately
-- against each authorized business Supabase, after review/backup. Existing rows and
-- bot control functions are preserved. No public object URLs or client policies.
CREATE TABLE IF NOT EXISTS public.crm_media_assets (
  business_id text NOT NULL,
  id uuid NOT NULL,
  phone_number text NOT NULL,
  actor text NOT NULL DEFAULT '',
  direction text NOT NULL CHECK (direction IN ('inbound','outbound')),
  kind text NOT NULL CHECK (kind IN ('image','audio','document','video','sticker')),
  mime_type text NOT NULL,
  filename text NOT NULL,
  size_bytes bigint NOT NULL DEFAULT 0 CHECK (size_bytes BETWEEN 0 AND 100000000),
  sha256 text CHECK (sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'),
  source_sha256 text CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[a-f0-9]{64}$'),
  source_filename text,
  source_mime_type text,
  voice_note boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','unavailable')),
  storage_key text,
  original_storage_key text,
  playback jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(playback)='object'),
  source_event_id text,
  meta_media_id text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(business_id,id),
  CHECK (length(business_id) BETWEEN 1 AND 63),
  CHECK (length(phone_number) BETWEEN 1 AND 200),
  CHECK (status <> 'ready' OR (storage_key IS NOT NULL AND sha256 IS NOT NULL AND size_bytes > 0))
);
ALTER TABLE public.crm_media_assets DROP CONSTRAINT IF EXISTS crm_media_assets_kind_check;
ALTER TABLE public.crm_media_assets ADD CONSTRAINT crm_media_assets_kind_check CHECK (kind IN ('image','audio','document','video','sticker'));
CREATE INDEX IF NOT EXISTS ix_crm_media_conversation ON public.crm_media_assets(business_id,phone_number,created_at);
ALTER TABLE public.crm_media_assets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.crm_media_assets FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
  FOREACH role_name IN ARRAY ARRAY['anon','authenticated'] LOOP
    IF to_regrole(role_name) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON TABLE public.crm_media_assets FROM %I',role_name);
    END IF;
  END LOOP;
  IF to_regrole('service_role') IS NOT NULL THEN
    GRANT SELECT,INSERT,UPDATE ON public.crm_media_assets TO service_role;
  END IF;
END $$;
DO $$ DECLARE role_name text; policy_name text; BEGIN
  IF to_regclass('storage.buckets') IS NOT NULL THEN
    INSERT INTO storage.buckets(id,name,public,file_size_limit)
      VALUES('crm-private-media','crm-private-media',false,100000000)
      ON CONFLICT(id) DO UPDATE SET public=false,file_size_limit=100000000;
  END IF;
  IF to_regclass('storage.objects') IS NOT NULL THEN
    FOREACH role_name IN ARRAY ARRAY['anon','authenticated'] LOOP
      IF to_regrole(role_name) IS NOT NULL THEN
        policy_name := 'crm_media_private_'||role_name;
        IF NOT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='storage' AND tablename='objects' AND policyname=policy_name) THEN
          EXECUTE format('CREATE POLICY %I ON storage.objects AS RESTRICTIVE FOR ALL TO %I USING (bucket_id <> ''crm-private-media'') WITH CHECK (bucket_id <> ''crm-private-media'')',policy_name,role_name);
        END IF;
      END IF;
    END LOOP;
  END IF;
END $$;
-- CRM PRIVATE MEDIA END
