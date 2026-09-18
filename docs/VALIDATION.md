# Validation record — 8 September 2026

Status: **implemented MVP, locally validated; production release checks remain open.**

## Guided setup update — 12 September 2026

- Full deterministic Python run: **50 passed, 2 skipped**. The skips require a live provider and the dedicated PostgreSQL environment. After adding the upgrade-manifest check, the focused calibration/Kubernetes run passed **12 tests**.
- Frontend unit tests: **4 passed**. Python lint, generated OpenAPI types, TypeScript compilation, and both container image builds passed.
- Chrome browser acceptance: **2 passed**. The existing upload → suite form → review → evaluate → publish → question → citation → feedback journey passes. The new grader journey saves an incomplete draft, reloads the selected workspace, completes ten examples through forms, explicitly reviews them, runs a simulated grader, and activates the result. Desktop/mobile screenshots were inspected; no browser errors or page overflow were detected in the passing run.
- Backend calibration tests cover missing/forged review input, workspace/member restrictions, invalid source selection, duplicate submissions, immutable completed records, passing/failed activation, configuration changes, source revocation/redaction, cancellation, resumed progress, and sanitized provider errors.
- All new calibration test ratings and review records are explicitly automated fixtures in isolated test databases. No live workspace calibration was created and no paid model check was run for this update. These tests do not establish human/AI agreement on the customer's documents.
- Local Kubernetes upgrade passed with backend/web `k8s-v3`. PostgreSQL applied `d05ca1100001`; API and web returned HTTP 200 and the five grader API paths are present. Backend (3/3) and web (1/1) were ready with zero restarts. Live mode remains `openai`; counts remained 2 accounts, 12 documents, and 4 workflows, with zero calibration records created in the live database. The application role can access the new table and still cannot delete audit events. A pre-upgrade database snapshot was saved to `.cache/kubernetes-backups/20260912-174110/before-v3.dump`; this is not a full restore drill.

Earlier baseline evidence and outstanding release requirements follow.

## Verified locally

| Check | Result | Scope |
|---|---|---|
| Python automated suite | 32 passed, 1 skipped | SQLite, deterministic provider; the skipped case requires a live API key |
| Python lint | Passed | Application, tests, migrations, fixtures, operational helpers |
| OpenAPI generation | Passed | Generated TypeScript contracts used by the frontend |
| TypeScript and production frontend build | Passed | React/React Flow bundle, self-hosted fonts |
| Frontend graph tests | 4 passed | Invalid cycles, branches, and connection rules |
| Browser acceptance | Passed | Chrome; full workflow journey, desktop and mobile navigation |
| Migration application | Passed on SQLite and PostgreSQL | PostgreSQL migrations executed on the fresh Kubernetes database |
| Compose configuration validation | Passed | Structural validation only; no running container deployment |
| Backup integrity helper | Passed automated test | Checksums and archive validation; not a restore drill |
| Kubernetes manifest tests | 2 passed | Shared storage, setup/runtime credential separation, generated-manifest consistency |
| Container image builds | Passed | Backend and web images built and loaded into kind |
| Kubernetes deployment | Passed | Kubernetes 1.35.8, cluster `relay-local`, namespace `relay`; all application containers ready |
| PostgreSQL migrations and application role | Passed in Kubernetes | Fresh database migrations; `relay_app` cannot update or delete audit events |
| Kubernetes API validation | Passed | Server-side dry run of all deployment resources |
| Browser journey against Kubernetes | Passed | Actual PostgreSQL/pgvector, Redis/Celery, Nginx, and deterministic model provider |
| Kubernetes application-Pod replacement | Passed | Document files, existing published workflow, and a new cited worker run verified after restart |

The browser test signs in, creates a workspace, uploads Markdown, inspects indexed text, creates a visual workflow, previews an answer, creates a two-case suite, exercises the review control, evaluates and publishes, asks a question, opens a citation, submits feedback, and reopens history. It checks for browser errors and mobile overflow, and verifies that the mobile navigation opens and closes. Test review records explicitly identify themselves as automated fixtures, never actual human reviews.

Backend tests cover workspace isolation, CSRF/session handling, login throttling, draft restrictions, stale report rejection, source deletion and redaction, immutable document snapshots, allowed agent tools, rejected external tools, invalid citations/graphs, empty or unsupported files, provider errors, bounded rate-limit retries, cancellation/deadlines, duplicate jobs, simulated worker recovery, retry exhaustion, retention, and grader calibration agreement/failure.

The worker recovery test simulates a crash between trace and checkpoint commits. Separately, the deployed Kubernetes application Pod was replaced and document files plus a new cited answer were verified afterward. That check does not cover a node failure or a crash during an in-flight provider call. The browser tests use the demo provider; their answers and timings do not establish real-model quality or latency.

## Outstanding release evidence

1. **Target-environment validation:** Docker's startup error was resolved, and the local Kubernetes deployment, PostgreSQL migrations, and restricted application role are now verified. The complete PostgreSQL CI test path, Compose-specific deployment, and the intended enterprise cluster still need their own validation.
2. **Live provider:** Supply the deployment's API key and model configuration. Run the opt-in live-provider test, a human-label grader calibration, and representative workflow evaluations. No live model test or paid model call was performed here.
3. **Human-reviewed examples:** The demo includes 60 generated fictional examples: 50 answerable, 5 unanswerable, and 5 adversarial. They are explicitly awaiting human review. At least 50 human-reviewed examples are still required by the release plan.
4. **Load measurement:** Run the supplied five-concurrent-user benchmark over 10,000 PostgreSQL chunks. Retrieval p95 below one second is a target, not a measured result here. Record full-answer latency separately with the live provider.
5. **Recovery drill:** Create a real backup, restore into a new isolated deployment, and verify documents, citations, an interrupted run, permissions, and audit history. A local application-Pod restart is a narrower persistence check and does not establish backup recoverability.
6. **Deployment review:** Verify enterprise HTTPS/cookies, secret provisioning, log access, storage quotas, monitoring, and retention against the actual environment. CI configuration is included; no remote CI run was triggered.

Use [README](../README.md) for local setup and [OPERATIONS](OPERATIONS.md) for release commands and evidence requirements. Passing the local suite does not complete the outstanding checks above.
