# Architecture and contracts

## Service boundaries

The React/TypeScript client communicates with `/api/v1` using server-managed session cookies and CSRF headers. The Python API owns authorization, immutable workflow versions, document revisions, evaluation review, and publication. LangGraph executes the validated graph. Job rows are a durable outbox; a local dispatcher or Celery worker claims them with a renewable lease. Redis transports Celery jobs; authoritative job/run state remains in SQL.

The browser receives run status via resumable server-sent events. Reconnection uses `Last-Event-ID`; clients also poll the run record as a fallback. Step events contain identifiers and timing; full step inputs/outputs are restricted to workspace administrators.

## Graph behavior

Exactly one input begins the graph. Every step must be reachable, every path must terminate in an answer, and cycles and parallel fanout are rejected. A condition has one `true` and one `false` outgoing edge. Model steps must follow retrieval or an agent on every path. A node carries its prompt, retrieval limit, bounded tool count, and condition predicate as applicable. The server validates independently of the visual editor.

Agent loops are internal to the agent node. The only registered tools are `knowledge_search` and `read_source`; source reading is limited to previously retrieved chunks. Both check workspace membership and the frozen knowledge revision. Models cannot register tools or execute arbitrary HTTP/code. Default limits are four tool calls, 60 seconds, and a 16,000-token generation/decision budget.

Every run uses one immutable workflow version and its model profile. Previews and evaluation examples use the same engine as employee questions. Conversation history is owned by the initiating employee and previous answers with revoked sources are excluded from context.

## Knowledge snapshots

Document replacements create separate records. A successful ingestion increments the workspace revision atomically; previous versions become superseded at that revision. Failed ingestion does not advance knowledge. Published assistants keep their approved revision while additions/replacements become candidates for a newly saved workflow version.

Deleting a document revokes it immediately, removes its chunks, blocks source access, and pauses affected published assistants. Historical run payloads are redacted at read time. Retention subsequently purges traces and checkpoint payloads; audit metadata remains. A paused assistant requires a new evaluated version using an accessible knowledge snapshot.

PostgreSQL retrieval applies workspace and revision filters to both semantic and lexical candidates before fusion. HNSW uses iterative scanning for filtered results. SQLite demo retrieval performs exact vector scoring in Python and is not the performance path.

## Publication gate

Workflow fingerprints bind the graph, prompts, knowledge revision, and model profile. Report fingerprints additionally bind the immutable suite content, thresholds, suite version, and review record. Publication verifies ownership, completed status, passing metrics, the fingerprint, latest suite version in its family, current deployment model configuration, and absence of revoked sources. Employee requests cannot select arbitrary draft versions.

Publishing an earlier eligible version implements rollback. The same gate applies; a historical version with revoked data or obsolete model configuration is ineligible. Saving a new version never changes the published pointer. Failed or stale reports cannot authorize publication through the API.

Deterministic citation validation checks existence and exact quoted text. The model grader separately estimates correctness and evidence support; it is not a proof of truth. Retrieval recall is measured against expected source documents within the first five retrieved chunks. A failed example prevents passing publication metrics. Suites must include answerable and expected-abstention cases. Threshold changes create a new suite version requiring review and evaluation.

Live publication additionally requires a grader calibration report based on at least ten distinct human-scored answers spanning poor and strong answers. Correctness and evidence-support mean absolute errors must each be at most 0.10. The report binds the reviewed dataset hash and grader identity. Administrators can prepare, review, execute, and activate checks through the workspace-scoped API and forms. The outbox runs calibration jobs with persisted per-example progress, bounded retries, cancellation, and an overall deadline. The database report fingerprint (or the legacy operator file hash), grader prompt revision, and execution revision are included in the workflow model profile, making calibration changes invalidate earlier approval. An active database check takes precedence over the legacy file even when stale. Source access and grader identity are rechecked when using an approval. Operators must increment revision identifiers when changing the built-in execution or grading prompts.

## Failure and recovery semantics

Claims are at-least-once. Job leases are renewed every 20 seconds and expire after 90 seconds. A worker restart can resume a LangGraph checkpoint; a committed completed step trace is reused if the worker died before the corresponding checkpoint commit. Database writes and ingestion are idempotent. A provider call can still be repeated if a process dies before its result is committed; exactly-once external billing is not promised.

Recovery stops after three job attempts by default (`MAX_JOB_ATTEMPTS`). An older claim cannot renew or complete a newer claim's job record. Ingestion checks the final document status under a lock before committing chunks. An exhausted recovery marks the target failed for an administrator to inspect and rerun.

Provider connection, rate-limit, timeout, and server failures retry at most twice with backoff inside the run budget. Other failures terminate with sanitized error messages. Time is checked before and after every model/tool call; OpenAI request timeouts are bounded by the remaining deadline. Cancellation is checked at step/tool boundaries, and any in-flight response is discarded once cancellation is observed.

## Interfaces

- `WorkflowDefinition`, workflow versions, runs, step traces, evaluation suites, and evaluation reports are Pydantic contracts exported through OpenAPI and generated into `frontend/src/generated/api.ts`. The React domain types use these generated contracts, and API response validation checks the same shapes.
- The API exposes accounts, workspaces/membership, document ingestion/chunks/revisions, workflow creation/duplication/versions/publication, runs/traces/events/cancellation, conversations, feedback, evaluation suites/reviews/reports, audit events, and operator status.
- `ModelProvider` defines embedding, grounded answering, tool decisions, and grading. The OpenAI implementation uses Responses structured output and function calling; the deterministic provider is explicitly marked as demo-only.
- File parsing and retrieval are separate functions in `backend/knowledge.py`; a new ingestion adapter must preserve document identity, workspace ownership, immutable revisions, and citation locations.
- `/health` checks API/database connectivity; `/ready` also checks Redis in Celery mode. `/api/v1/operations` is restricted to the system administrator and reports queue age/status and recent run failures.

Audit records are append-only under the Docker application database role. Database administrators still control the database; this is not cryptographically tamper-proof storage.
