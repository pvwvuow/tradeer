# Security

## Secrets

- Never commit tokens, passwords, broker credentials, account numbers, `.env` files, logs, databases, crash reports or model files.
- Account passwords, API keys and bot tokens are stored only in Windows Credential Manager through `keyring` (service `MT5TradingWorkstation`, one entry per `profile/login@server`). Account profiles on disk (`account.json`) never contain a password. Every password is registered with the log masker the moment it is read or saved, and the connection report from Connection Diagnostics is masked as well.
- The app uses only the Supabase **anon** key with Row Level Security (`user_id = auth.uid()`), never the service-role key.
- Logs and crash reports pass through a redaction filter before anything is written (Phase 2): registered secret values, known token formats (GitHub, `sk-` keys, JWTs, Telegram, AWS, Slack, Bearer), sensitive `key=value` pairs and passwords inside URLs are replaced with `***`. Exports and debug bundles will use the same filter. `python -m app --crash-test` proves it on any build.
- LLM requests never contain credentials or account passwords.
- If a credential is ever pasted into a chat, an issue or a commit, revoke it immediately and create a new one.

## Reporting a vulnerability

Report it privately to the repository owner. Do not open a public issue that contains a secret or an exploit.
