# Completion: bot management and history on the central connection

The four-brand migration is already active and committed. The old inactive
Briosos-only publication is not the active deployment. Do not activate the old
pilot, restrict CRM scope back to Briosos, or repeat its cutover runbook.

The remix retained the old dashboard proxy and direct Supabase log reader. Both
looked for per-brand frontend variables containing remix placeholders, although
the central registry already held all four real dashboard credentials.

## Shared transport, unchanged bot behavior

`/v1/bot-management/{business}/...` reuses each registered bot origin and its
existing private DASHBOARD_API_KEY. It forwards only the old dashboard API's
explicit instruction/history/catalog/file operations. The bot still owns Gemini,
GitHub commits, storage, catalog validation and its existing SI deployment branch.
No dashboard/Supabase/Meta key goes to the browser or needs to be copied into the
frontend. No new configuration variable, SQL migration or bot restart is needed.

The frontend uses its existing CRM_BACKEND_URL, CRM_JWT_SECRET and canonical
operator claims. Management authorization additionally retains the original
login_profile's brand: a shared Tanaka/Velvet inbox does not permit cross-brand
editing. Admin retains its authorized scope; Memo's two profiles manage the same
Memo's bot. Historical logs use the central source projection with the original
2000/5000 limits and enforce the operator's current Memo's queue. Account/password
resolution remains server-only and unchanged.

Multipart uploads and downloads stream through the central API; bounded request
handling reads the existing per-business catalog size limit from its registered
snapshot (including the configured 200 MiB businesses), preserving the bot's
validation. No new setting is requested. No database transaction is held
across the upstream transfer. Failed/uncertain mutations are never retried by the
proxy. HTML error documents are not rendered as instructions or raw catalog error
text. The manual editor stays locked on failed reads and saves the exact edited
text through the existing skip_format operation (no extra Gemini formatting).
AI-assisted saves wait for the original bounded background job using only GET;
they never report the initial 202 as a completed GitHub save or replay the POST.

Fresh non-central installations retain the previous legacy frontend transport.
Central management endpoints fail closed until central settings/credentials are
available. There are no schema/bootstrap/upgrade changes.

Deploy only the two CRM APIs and publish Chatty 3.0's current GitHub version in
Lovable. No Railway variables, activation, migration, bot/worker deployment or
secret copy is required. Keep Chatty 2.0 and original databases intact.

Source-reviewed, with existing-style authorization/allowlist regression cases
added but not executed. No local app, tests, lint, typecheck, build, browser
automation, customer sends, SI edits or catalog mutations were run for validation.
Deployment readiness is not end-to-end confirmation of editing/upload behavior.
After publishing, open Instruction, its Git history, Catalog and historical logs
for an authorized account. Confirm reads before making any deliberate business
edit. Never save an empty/error document as the active instruction.
