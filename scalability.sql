-- Run this once in the Supabase SQL editor before enabling queued workers.
-- It adds idempotency storage and indexes used by the scalable webhook flow.

CREATE TABLE IF NOT EXISTS public.processed_webhook_events (
  source character varying NOT NULL,
  event_id character varying NOT NULL,
  phone_number character varying,
  status character varying NOT NULL DEFAULT 'received',
  received_at timestamp with time zone DEFAULT now(),
  processed_at timestamp with time zone,
  error text,
  CONSTRAINT processed_webhook_events_pkey PRIMARY KEY (source, event_id)
);

CREATE INDEX IF NOT EXISTS idx_message_logs_phone_created_at
ON public.message_logs(phone_number, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_states_chatwoot_conversation_id
ON public.conversation_states(chatwoot_conversation_id)
WHERE chatwoot_conversation_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_states_active_chatwoot_conversation_id
ON public.conversation_states(chatwoot_conversation_id)
WHERE chatwoot_conversation_id IS NOT NULL;
