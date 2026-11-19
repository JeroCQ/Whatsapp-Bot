-- Shared configuration in the selected Tanaka project. Does not alter its bot tables.
CREATE TABLE IF NOT EXISTS public.chatty_runtime_businesses (
  business_id text PRIMARY KEY CHECK (business_id ~ '^[a-z][a-z0-9_]{0,39}$'),
  bot_api_url text NOT NULL,
  target_prefix text NOT NULL UNIQUE CHECK (target_prefix = '' OR target_prefix ~ '^chatty_[a-z][a-z0-9_]{0,39}_$'),
  channel_id text,
  mode text NOT NULL DEFAULT 'legacy' CHECK (mode IN ('legacy','central')),
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  source_configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_commit text NOT NULL DEFAULT '',
  migration jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.chatty_runtime_businesses ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chatty_runtime_businesses FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.chatty_runtime_businesses TO service_role;
INSERT INTO public.chatty_runtime_businesses(business_id,bot_api_url,target_prefix) VALUES
 ('tanaka','https://bot-tanaka-web-production.up.railway.app',''),
 ('velvet','https://bot-velvet-web-production.up.railway.app','chatty_velvet_'),
 ('memos','https://memos-whatsapp-bot-production.up.railway.app','chatty_memos_'),
 ('briosos','https://bot-velvet-web-production-47d6.up.railway.app','chatty_briosos_')
ON CONFLICT(business_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.chatty_runtime_services (
  role text PRIMARY KEY CHECK (role IN ('commercial-api','commercial-worker','central-worker','pilot-api','pilot-worker')),
  mode text NOT NULL DEFAULT 'legacy' CHECK (mode IN ('legacy','central')),
  source_configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
  frozen boolean NOT NULL DEFAULT false,
  epoch text NOT NULL DEFAULT '',
  paused_epoch text NOT NULL DEFAULT '',
  api_url text NOT NULL DEFAULT ''
);
ALTER TABLE public.chatty_runtime_services ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chatty_runtime_services FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.chatty_runtime_services TO service_role;
INSERT INTO public.chatty_runtime_services(role) VALUES
 ('commercial-api'),('commercial-worker'),('central-worker'),('pilot-api'),('pilot-worker') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS public.chatty_runtime_tokens (
  id text PRIMARY KEY,
  token text NOT NULL CHECK (length(token) >= 32)
);
ALTER TABLE public.chatty_runtime_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chatty_runtime_tokens FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.chatty_runtime_tokens TO service_role;

CREATE TABLE IF NOT EXISTS public.chatty_migration_runs (
  epoch text PRIMARY KEY,
  state text NOT NULL CHECK (state IN ('preparing','frozen','committed','aborted')),
  progress jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.chatty_migration_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chatty_migration_runs FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.chatty_migration_runs TO service_role;

NOTIFY pgrst, 'reload schema';
