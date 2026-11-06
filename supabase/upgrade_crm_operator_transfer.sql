-- Existing Memo's BOT Supabase only (or a disposable test bot): review and back up first.
-- Requires the existing CRM control tables/functions; does not create clients, change AI flags or migrate CRM data.
BEGIN;
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
REVOKE ALL ON FUNCTION public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb) FROM PUBLIC;
DO $$ DECLARE r text; BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF to_regrole(r) IS NOT NULL THEN EXECUTE format('REVOKE ALL ON FUNCTION public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb) FROM %I',r); END IF;
 END LOOP;
 IF to_regrole('service_role') IS NOT NULL THEN GRANT EXECUTE ON FUNCTION public.crm_control_dispatch(text,text,text,text,text,bigint,jsonb) TO service_role; END IF;
END $$;
COMMIT;
