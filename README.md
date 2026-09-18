# Relay — Enterprise AI Workspace

**Start here.** Relay provides private personal agents and evaluated shared assistants, using React, FastAPI, LangGraph, Celery, PostgreSQL/pgvector and local BGE embeddings.

## Using Relay

1. Open **http://localhost:8088** for local Kubernetes and sign in with your administrator-created account.
2. Open **Agents → Create agent**. Set its name, instructions, conversation starters, workspace documents and permitted tools. Save and Preview, or select it in Assistant. Private agents show **Not reviewed** and are usable immediately by their owner.
3. Open **Connections** for your own Gmail and Google Drive accounts. An operator must first complete [Google setup](docs/GOOGLE_CONNECTIONS.md). Drive's **Choose files** selects individual Google Docs, text PDFs, TXT and Markdown files (Available soon).
4. Ask questions in **Assistant**. Open citation chips to inspect exact evidence and retrieval timestamps. Private Google evidence and detailed runs belong to the executing user. Disconnecting an account or removing a selected file invalidates access.
5. Ask an agent to prepare an email. Edit the recipients, subject and body in Relay, then explicitly click **Save draft to Gmail** to create an unsent draft. Relay cannot send. If a save is uncertain, use **Check draft save** to reconcile the same operation instead of creating another copy.
6. Choose **Request sharing** to submit an immutable candidate in **Workflows**. Your own agent/conversations remain private; colleagues use their own connections with an approved shared agent.

Administrators upload documents in **Knowledge**, manage shared workflows in **Workflows**, inspect operational runs in **Runs**, and configure tool policies in **Connections**. Uploaded-source deletion immediately pauses affected releases.

## Evaluations

Create and review question/reference/source/abstention examples in **Evaluations**. The **Grader check** tab guides you through human ratings, checking and activating a calibrated grader. Human review records are never generated automatically.

Save a new workflow version after activating the grader check, evaluate it and publish its matching passing report. Approvals bind the instructions, tools, policy, model profile, knowledge revision, suite and fixture version. Earlier reports remain unchanged. Restoring a previous version requires current publication eligibility.

External-tool evaluation cases must include `fixture_version`, reviewed `tool_fixtures` (`tool`, `arguments`, `sources`), and `expected_tools`. Start with the [sanitized example suite](docs/external-tool-suite.example.json), adapt it and have a person review it before importing it. Fixture reports are distinguished from live connection checks. Test fixtures are not human-reviewed release evidence.

## Models and privacy

Generation retains **CHAT_MODEL**. The code default is `gpt-5.6-luna`; upgrades preserve the actual deployed value. Grading uses **gpt-5.6-terra** with the existing grader prompt, reasoning effort and limits. Embeddings use local **BAAI/bge-small-en-v1.5**, pinned to revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`; the image contains its verified ONNX artifact.

OpenAI receives necessary inference/grading context and charges for inference. Public search sends the user's original question to configured public engines; private tool results are never added to that query. No paid MCP intermediary, embedding API or search subscription is required. Google verification costs may apply.

Demo providers, synthetic runtime embeddings, seeded startup content and publication bypasses are removed. Startup requires a valid OpenAI key and embedding artifact. Retired `MODEL_PROVIDER=demo` or `ALLOW_DEMO_PUBLICATION` settings fail explicitly. Historical data stays stored; old synthetic releases/jobs cannot run or silently turn into live calls.

## Startup and upgrade

For the existing local Kubernetes deployment, start Docker Desktop and wait for its Linux engine, then:

```powershell
uv run python -m scripts.deploy_kubernetes --upgrade
```

This builds current images, snapshots the database/documents/configuration/secrets under `.cache/kubernetes-backups`, migrates and deploys private MCP/search services. It preserves accounts and the running chat model. Use `--skip-build` only after building current images. The old `--live` flag is accepted for compatibility; fresh installations always use live inference.

For a new Kubernetes installation, set `OPENAI_API_KEY`, a real `BOOTSTRAP_EMAIL` and a strong `BOOTSTRAP_PASSWORD` in the shell, then run `uv run python -m scripts.deploy_kubernetes`. There is no default password. Integration encryption/signing keys are generated once and retained.

For Compose, copy `.env.compose.example` to `.env.compose`, supply its passwords, API key and generated connection/search keys, then:

```powershell
docker compose --env-file .env.compose up --build -d
docker compose --env-file .env.compose exec api python -m backend.manage bootstrap
```

Compose opens at **http://localhost:8080**. Nginx serves React and forwards API requests to FastAPI; database, Redis, MCP and search ports remain private.

For native development: configure `.env` from `.env.example`, remove retired settings, run `uv sync --frozen`, `uv run python -m scripts.prepare_embeddings`, `uv run python -m backend.manage migrate` and `uv run python -m backend.manage bootstrap`. Start the API and Vite frontend separately. Native startup does not start MCP/search containers automatically.

The local kind installation is a single-node development deployment, not a high-availability enterprise cluster. Enterprise rollout additionally requires HTTPS/secure cookies, enforced private-network policies, secret management, backup/restore and capacity verification. See [Kubernetes operations](ops/kubernetes/README.md).

## Tests and backups

```powershell
uv run ruff check backend tests scripts migrations
uv run pytest -q
uv run python -m backend.manage openapi
cd frontend
npm run generate:api
npm run build
npm test
$env:PLAYWRIGHT_CHANNEL='chrome'
npm run test:e2e
```

Browser tests use isolated directories and `tests.server_app`. Deterministic substitutes and synthetic datasets live only in `tests/`, excluded from deployment images. PostgreSQL tests require `relay_ci_test`; `uv run python -m tests.benchmark` requires `relay_benchmark`. Synthetic-vector timings do not establish live model quality.

Back up PostgreSQL, `/data`, configuration and the connection encryption key together. Secret backups are sensitive: restrict access and encrypt off-computer copies. Verify restoration into an isolated database before relying on backups. Losing the encryption key requires reconnecting Google accounts. Deleting the kind cluster destroys its local storage.

Before production release, complete authorized live Google journeys, a live Terra check, genuinely human-reviewed calibration/evaluation examples, and corpus/concurrency measurement. Automated tests do not replace these checks.
