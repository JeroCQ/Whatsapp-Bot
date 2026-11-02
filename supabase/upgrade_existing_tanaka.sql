-- Idempotent compatibility upgrade for the EXISTING paid Tanaka Supabase project.
-- Review the duplicate-conversation query before running this file. It must return
-- zero rows, otherwise resolve those stale IDs before creating the unique index.

SELECT chatwoot_conversation_id, count(*) AS duplicate_count
FROM public.conversation_states
WHERE chatwoot_conversation_id IS NOT NULL
GROUP BY chatwoot_conversation_id
HAVING count(*) > 1;

-- The current runtime stores one-time follow-up cancellation tokens here. Older
-- Tanaka schemas predate this column, so CREATE TABLE IF NOT EXISTS is not enough.
ALTER TABLE public.conversation_states
  ADD COLUMN IF NOT EXISTS follow_up_token varchar,
  ADD COLUMN IF NOT EXISTS customer_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS order_summary text;

CREATE INDEX IF NOT EXISTS idx_message_logs_phone_created_at
  ON public.message_logs(phone_number, created_at DESC, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_states_active_chatwoot_conversation_id
  ON public.conversation_states(chatwoot_conversation_id)
  WHERE chatwoot_conversation_id IS NOT NULL;

-- Catalog uploads and sends use this exact public bucket. This preserves an
-- existing bucket and only corrects its public flag when necessary.
INSERT INTO storage.buckets (id, name, public)
VALUES ('catalogos', 'catalogos', true)
ON CONFLICT (id) DO UPDATE SET public = true;

-- Read-only verification result: every value should be true after the upgrade.
SELECT
  to_regclass('public.customers') IS NOT NULL AS has_customers,
  to_regclass('public.conversation_states') IS NOT NULL AS has_conversation_states,
  to_regclass('public.message_logs') IS NOT NULL AS has_message_logs,
  to_regclass('public.processed_webhook_events') IS NOT NULL AS has_webhook_events,
  EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'conversation_states'
      AND column_name = 'follow_up_token'
  ) AS has_follow_up_token,
  EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'conversation_states'
      AND column_name = 'customer_data'
  ) AS has_customer_data,
  to_regclass('public.uq_conversation_states_active_chatwoot_conversation_id') IS NOT NULL
    AS has_unique_chatwoot_index,
  EXISTS (
    SELECT 1 FROM storage.buckets WHERE id = 'catalogos' AND public
  ) AS has_public_catalog_bucket;
