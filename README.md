# Whatsapp-Bot
Customer Service Bot

## Business-specific files in Railway

Keep the code unchanged and save each business file in its own Railway variable.
Business-file variable names must start with `BUSINESS_FILE_`. Their values may
be the file's UTF-8 text or an HTTP(S) URL that returns UTF-8 text.

Reference only the files needed by the current business from `SYSTEM_PROMPT`:

```env
SYSTEM_PROMPT=You are the Cafe Uno assistant. Use this catalog: {{BUSINESS_FILE_CAFE_UNO}}
BUSINESS_FILE_CAFE_UNO=https://example.com/cafe-uno.txt
BUSINESS_FILE_STORE_TWO=https://example.com/store-two.txt
```

To switch businesses, change only `SYSTEM_PROMPT`:

```env
SYSTEM_PROMPT=You are the Store Two assistant. Use this catalog: {{BUSINESS_FILE_STORE_TWO}}
```

Both file variables remain saved in Railway, so switching back does not require
entering them again. The existing database inventory is available through the
`{{inventory}}` placeholder. `MAX_BUSINESS_FILE_BYTES` optionally changes the
per-file limit from its default of 1,000,000 bytes.
