-- Keep persisted message authors aligned with the roles consumed by bot.py.
-- PostgreSQL assigned this name to the original inline CHECK constraint.
ALTER TABLE public.message_logs
  DROP CONSTRAINT IF EXISTS message_logs_role_check;

ALTER TABLE public.message_logs
  ADD CONSTRAINT message_logs_role_check
  CHECK (role IN ('user', 'model', 'system', 'asesor'));
