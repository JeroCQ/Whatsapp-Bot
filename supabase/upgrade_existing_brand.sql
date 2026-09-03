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

INSERT INTO storage.buckets (id, name, public)
VALUES ('catalogos', 'catalogos', true)
ON CONFLICT (id) DO UPDATE SET public = true;

CREATE TABLE IF NOT EXISTS public.catalog_assets (
  business_id text NOT NULL,
  catalog_id text NOT NULL CHECK (catalog_id ~ '^catalogo_[a-z0-9_]{1,52}$'),
  public_name text NOT NULL CHECK (length(trim(public_name)) BETWEEN 1 AND 120),
  description text NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 500),
  media_type text NOT NULL DEFAULT 'document' CHECK (media_type IN ('document', 'image')),
  filename text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, catalog_id)
);
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
