ALTER TABLE public.conversation_states
  ADD COLUMN IF NOT EXISTS customer_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS order_summary text;
