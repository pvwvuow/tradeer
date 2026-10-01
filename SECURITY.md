# Security

## Secrets

- Never commit tokens, passwords, broker credentials, account numbers, `.env` files, logs, databases, crash reports or model files.
- Account passwords, API keys and bot tokens are stored only in Windows Credential Manager through `keyring` (from Phase 3).
- The app uses only the Supabase **anon** key with Row Level Security (`user_id = auth.uid()`), never the service-role key.
- Logs, exports, crash reports and debug bundles pass through a redaction filter (from Phase 2).
- LLM requests never contain credentials or account passwords.
- If a credential is ever pasted into a chat, an issue or a commit, revoke it immediately and create a new one.

## Reporting a vulnerability

Report it privately to the repository owner. Do not open a public issue that contains a secret or an exploit.
