# Whatsapp-Bot

Customer service bot with business-specific configuration stored in Railway.

## Reusing the bot for multiple businesses

The code does not need to be edited when deploying it for another business. Set
the following variables on each Railway service/environment so Railway retains
that business's configuration:

- `SYSTEM_PROMPT`: the complete instructions for that business.
- `BUSINESS_FILE_<ALIAS>`: a UTF-8 text knowledge source. Its value can be an
  HTTP(S) URL, a path available to the deployment, or inline text prefixed with
  `text:`.
- `MAX_BUSINESS_FILE_BYTES` (optional): maximum size for each file; defaults to
  `1000000` bytes.

Reference a file in `SYSTEM_PROMPT` with `{{file:ALIAS}}`. The alias is
case-insensitive when matched to its environment variable. The existing live
inventory can be inserted with `{{inventory}}`.

For example:

```env
SYSTEM_PROMPT=You are the assistant for Cafe Uno. Use only this catalog:\n{{file:catalog}}
BUSINESS_FILE_CATALOG=https://example.com/cafe-uno-catalog.txt
BUSINESS_FILE_POLICIES=text:Returns are accepted within 14 days.
```

To switch the prompt to policies without changing code or deleting the saved
catalog variable:

```env
SYSTEM_PROMPT=You are the assistant for Cafe Uno. Follow these policies:\n{{file:policies}}
```

Create a separate Railway service or environment for another business and give
it its own `SYSTEM_PROMPT` and `BUSINESS_FILE_*` values. Switching back to the
previous Railway environment restores its saved variables. Only files named in
the active prompt are loaded and sent to Gemini.

Knowledge sources must be UTF-8 text (for example TXT, Markdown, CSV, or JSON).
Binary PDF or Word files should first be exported to text or exposed through a
text-producing endpoint.

## Webhook setup

Evolution API can send `messages.upsert` events to `/webhook`. The bot also
accepts Chatwoot `message_created` events at `/chatwoot-webhook`; `/` is accepted
as a compatibility webhook URL as well. Outgoing messages are ignored to prevent
reply loops.

`DATABASE_URL` is optional. Without it, the bot runs without persisted pause/VIP
state and uses the files in `SYSTEM_PROMPT` instead of database inventory. Set a
PostgreSQL `DATABASE_URL` when persisted customer state and the `products` table
are needed.

The outbound Evolution API request uses the current `number`, `text`, and
`delay` fields. `GEMINI_MODEL` can override the default `gemini-2.5-flash` model.
