-- Run once on existing projects created before the Chatwoot handoff roles existed.
ALTER TABLE public.message_logs
  DROP CONSTRAINT IF EXISTS message_logs_role_check;

ALTER TABLE public.message_logs
  ADD CONSTRAINT message_logs_role_check
  CHECK (role IN ('user', 'model', 'system', 'asesor'));
