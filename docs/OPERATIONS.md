# Deployment and operations

## Release procedure

Google integration credentials must be supplied through environment variables; the application has no built-in Google credentials. Keep real values in ignored `.env` files or your deployment secret store, and use the tracked `.env.example` files as templates. When migrating a legacy demo installation, set `RETIRED_DEMO_PASSWORD` to its old demo password so migration can identify and disable those accounts. Leave it blank for new installations; use a separate, strong `BOOTSTRAP_PASSWORD` for the replacement administrator.

1. Run Python tests, lint, frontend build/tests, and the browser acceptance test. Regenerate OpenAPI types when contracts change.
2. Run the same tests against the dedicated PostgreSQL test database. Build containers from the locked Python/npm dependencies.
3. Configure HTTPS, `APP_ORIGIN`, secure cookies, model API credentials, a pinned model configuration, and separate database passwords. Keep `.env` files out of source control and provision them using your secret-management process. Calibrate the grader after startup, before publishing.
4. Start PostgreSQL and Redis, then the migration service. Start API, worker, scheduler, and web only after migrations succeed. Bootstrap the administrator once.
5. Upload the pilot corpus. Review the reference examples against the real documents and calibrate grader scores against human labels. Run live evaluations through both the deterministic RAG template and the bounded-agent template.
6. Publish the passing version, verify an employee account in the correct workspace, and run the five-user/10,000-chunk benchmark on documented target hardware.

The Compose setup binds the web port to loopback. To provide enterprise access, put an approved HTTPS reverse proxy in front of it. Set `APP_ORIGIN` to the exact browser origin. Do not use demo credentials or `ALLOW_DEMO_PUBLICATION=true` with real users. Demo and live model profiles are different; changing embedding models requires reingestion and a new evaluated workflow version.

## Grader calibration

The recommended path is **Evaluations → Grader check**. Administrators prepare and save drafts, select authorized source passages, enter their own correctness/support ratings, and explicitly record a review before starting the background check. Results persist across browser refreshes and worker recovery; queued/running checks can be cancelled. Both mean absolute errors must be at most 0.10. A passing result must explicitly be activated with **Use for this workspace**, followed by new workflow versions and evaluations. Live checks incur model usage.

The database stores drafts, frozen review evidence, per-example progress, reports, and administrative audit events. Include these tables in database backups. Only workspace administrators can inspect or change these records. Revoked sources redact affected records and invalidate approval; grader-identity changes also invalidate approval. Completed checks are immutable: revisions create new records. An active in-app record takes precedence over the legacy file and cannot silently fall back to it when stale.

The command-line path below remains an optional operator alternative. Production startup permits administrators to configure calibration in-app; publication still fails closed without valid calibration.

Prepare a JSON dataset matching `python -m scripts.calibrate_grader --schema`. Have a reviewer score at least ten distinct question/answer examples on correctness and evidence support from 0 to 1. Include poor scores at or below 0.25 and strong scores at or above 0.75 for both metrics. Record the real reviewer and review date. Evaluate on examples representative of the pilot corpus, including plausible unsupported claims.

With the configured live provider, the calibration helper compares model scores with human labels. It fails if either mean absolute error exceeds 0.10. For a container deployment using this optional path, put the reviewed dataset in `artifacts` and run:

```sh
docker compose --env-file .env.compose run --rm --no-deps -v "${PWD}/artifacts:/artifacts:ro" api python -m scripts.calibrate_grader /artifacts/reviewed-grader-examples.json --output /data/grader-calibration.json
```

Set `GRADER_CALIBRATION_FILE=/data/grader-calibration.json` in the deployment environment. This command incurs hosted model usage. Calibration is an operational review artifact; only trusted operators should write it. Preserve the report and reviewed dataset with your release evidence. Changing the grader, its prompt revision, or the calibration report requires fresh workflow versions and evaluations. The example suite shipped with the demo is separate from this human-scored grader dataset.

## Monitoring

Monitor `/health` and `/ready` through the internal reverse proxy. Alert when readiness fails, queue age exceeds the expected run budget, failed jobs accumulate, or the proportion of failed/timed-out runs increases. The administrator-only operations endpoint provides queue counts/age and failures in the last 24 hours. The Runs view provides per-step evidence and errors; Evaluations records quality and p95 answer latency. Forward container logs to your operational logging service; raw provider exceptions and credentials are not emitted.

Start one scheduler and a worker with concurrency five. Do not expose PostgreSQL or Redis outside the private service network. Use resource and storage quotas appropriate to the pilot corpus. The local SQLite worker is for development, not production availability testing.

## Retention and revocation

Default detailed-payload retention is 30 days (`TRACE_RETENTION_DAYS`). Celery beat runs `workspace.purge` daily. Operators can run it immediately:

```sh
docker compose --env-file .env.compose exec api python -m backend.maintenance purge
```

This removes expired/revoked run questions, answers, source snapshots, trace payloads, event payloads, and LangGraph checkpoints. It retains run metadata, timing, evaluation metrics, and audit events. Evaluation references and submitted feedback have their own purpose and are not purged by this command; apply the enterprise's data-retention policy to those datasets before a production pilot. Source deletion takes effect immediately at all application reads, before the purge task runs.

## Backup

The backup helper quiesces the application and workers, exports PostgreSQL including checkpoints, archives original document files and the configured grader-calibration report, computes SHA-256 checksums, and restarts services in a `finally` block. It creates files under the specified destination and does not remove previous backups. Restore places the calibration report at `/data/grader-calibration.json`; use that path in the restored deployment configuration.

```sh
uv run python scripts/backup.py --env-file .env.compose --output artifacts/backups
uv run python scripts/verify-backup.py artifacts/backups/<timestamp>
```

Backups contain private company information. Store them in the enterprise's encrypted backup location with its access controls. Redis is deliberately excluded: queued work is recoverable from SQL job records. Protect the operator configuration separately; it is not included in the backup.

## Restore drill

The restore helper refuses any existing Compose project or volume and requires a project name beginning `relay-restore-`. It restores into new volumes, runs migrations, starts the backend services, and checks readiness. It never replaces the original deployment.

```sh
uv run python scripts/restore.py artifacts/backups/<timestamp> --env-file .env.compose --project relay-restore-drill-001
```

Build the regular backend image first. After the helper succeeds, point an isolated web instance at the restored API and manually verify document contents, a cited answer, a resumed interrupted run, workspace isolation, and audit history. Record recovery duration and maximum data loss against your recovery objectives. Checksums alone do not establish recoverability.

## Rollback and failure handling

- To roll back an assistant, select an earlier passing evaluation and publish that version. Revoked sources, changed model configuration, and newer suite versions can invalidate eligibility.
- To roll back application code, use a previously tested container image and verify migration compatibility. Do not blindly downgrade a database containing new data; restore into an isolated environment first.
- A worker crash leaves a leased job. After lease expiry, the dispatcher retries it and LangGraph restores persisted steps. Provider calls may repeat if the process died before committing their result.
- Failed ingestion can be retried from Knowledge after correcting the file/provider issue. Failed runs are re-run as new linked runs; the original audit trail remains.
- Human review, security review, actual model validation, backup restore, and target load tests are release evidence, not results that can be inferred from a working demo.
