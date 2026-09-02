-- Existing-project repair only. The same statements are already part of the
-- canonical supabase/bootstrap.sql for all new brand projects. Run this once in
-- an EXISTING Velvet or Memo's project created with an older bootstrap.
-- The runtime stores successful file-delivery markers as `system`
-- and human Chatwoot replies as `asesor`; without these roles, catalog markers
-- fail and the bot can resend the same catalog on every turn.

ALTER TABLE public.message_logs
  DROP CONSTRAINT IF EXISTS message_logs_role_check;

ALTER TABLE public.message_logs
  ADD CONSTRAINT message_logs_role_check
  CHECK (role IN ('user', 'model', 'system', 'asesor'));

-- Verification: the returned definition must list all four roles.
SELECT pg_get_constraintdef(oid) AS message_logs_role_constraint
FROM pg_constraint
WHERE conrelid = 'public.message_logs'::regclass
  AND conname = 'message_logs_role_check';
