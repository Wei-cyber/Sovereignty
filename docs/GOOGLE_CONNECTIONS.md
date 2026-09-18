# Gmail and Google Drive setup

Relay bundles two private MCP servers. These are application integrations, not Codex plugins; no third-party MCP subscription is required.

## Google Cloud configuration

1. Select your organization's Google Cloud project. Enable **Gmail API**, **Google Drive API** and **Google Picker API**.
2. Configure the OAuth consent screen, support contact, authorized domain, privacy policy and data-use details. Choose Internal only if your organization/users qualify; otherwise configure External and authorized test users during development.
3. Create a **Web application** OAuth client. Register the exact callback: `http://localhost:8088/api/v1/connections/google/callback` for local Kubernetes, port 8080 for Compose, or `https://YOUR-HOST/api/v1/connections/google/callback` for a secured deployment. It must match `APP_ORIGIN`; Connections shows this URL.
4. Add the application origin to authorized JavaScript origins. Create a Picker browser API key, restricted to your actual origins and required Picker API. Record the numeric Google Cloud project number.
5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_PICKER_API_KEY`, and `GOOGLE_PROJECT_NUMBER` on the server. Never expose the client secret or refresh tokens to frontend configuration.

Gmail initially requests `openid`, `email` and `gmail.readonly`. **Enable draft saving** incrementally requests `gmail.compose`. Google compose also permits sending; Relay independently restricts its adapter/registry to unsent draft creation. Sending, deleting, mailbox changes, attachments and calendar operations are not implemented. Drive requests `drive.file` and restricts retrieval to explicitly selected individual files, without whole-Drive access or modification.

Google classifies Gmail readonly and compose as restricted scopes. Verification and a security assessment may apply depending on audience, distribution and storage/transmission of restricted data. Review [Google's Gmail requirements](https://developers.google.com/workspace/gmail/api/auth/scopes) and [OAuth verification](https://support.google.com/cloud/answer/9110914), including testing-mode limitations. Local sign-in success does not establish production approval.

## Server secrets

Kubernetes v4 creates `relay-integrations` once with `CONNECTION_ENCRYPTION_KEY` (Fernet), `MCP_SIGNING_KEY`, `SEARXNG_SECRET` and the four Google settings. Existing keys are retained. Set the four Google variables in your private shell and run:

```powershell
uv run python -m scripts.configure_google
```

This updates only the Google settings in the dedicated local cluster and restarts affected services. It does not authorize accounts or access messages/files. For Compose put these settings in `.env.compose` and recreate affected containers.

To generate a Compose Fernet key: `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Generate separate signing/search secrets with `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`. Keep values private, stable and backed up with encrypted account data.

MCP uses private Streamable HTTP with signed 60-second user/workspace/tool capabilities. Do not expose MCP or search through public ingress. Enterprise Kubernetes must enforce the supplied NetworkPolicy through its CNI; default kind networking may not enforce it. Add service TLS if required by the deployment network. Search uses a pinned SearXNG image and public engines; upstream rate limits can affect availability.

## Connection checks

- Connect Gmail in **Connections**, then **Check connection**. Enable draft saving separately if wanted.
- Connect Drive, **Choose files** in Picker, and select up to 100 Google Docs, text PDFs, TXT or Markdown files. Folders and unsupported files are rejected.
- Create a personal agent with the appropriate tools; ask a question and inspect its citation using authorized test accounts.
- Ask for a draft, edit it, click **Save draft to Gmail**, then inspect Gmail Drafts. Nothing is sent. For uncertain saves use **Check draft save**, preserving the operation ID.
- Remove a selected file or disconnect an account; earlier evidence must become unavailable. Reconnect revoked/expired access. Relay disconnect clears its local access without globally revoking other apps or the other Google connection.

Only Google Picker receives the short-lived access token it needs, when the user opens the file picker. Refresh tokens are encrypted in PostgreSQL. Provider credentials stay out of model prompts, traces and access logs. Unconfigured connections remain visibly unavailable.

## Returning from Google says “Please sign in”

Relay v4.2 fixes a session-cookie issue on Google's return redirect. After upgrading, sign out of Relay, sign back in at the same `APP_ORIGIN` shown in Connections, and start **Connect account** again. Existing browser cookies keep their old attributes until a new login. Do not reuse an old callback URL; authorization codes and callback state expire and can be used only once.

Session cookies use `HttpOnly` and `SameSite=Lax` so a top-level GET return from Google can carry the existing login. HTTPS deployments also use `Secure`. State validation, expiry, user/workspace binding and CSRF checks on application writes remain enforced. If the error persists, verify the browser is using the exact host configured in `APP_ORIGIN` (for local Kubernetes, `http://localhost:8088`, not `http://127.0.0.1:8088`).
