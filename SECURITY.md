# Security

Do not commit tokens, passwords, broker credentials, account numbers, `.env` files, logs, databases, or model artifacts. Future account credentials belong in Windows Credential Manager through `keyring`. The app must use only Supabase anon keys with RLS, never a service-role key. Revoke any credential pasted into chat or source control immediately.

Report vulnerabilities privately to the repository owner. Do not open a public issue containing a secret or exploit.
