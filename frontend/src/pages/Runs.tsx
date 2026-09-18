import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Clock3,
  Copy,
  Download,
  Play,
  Search,
  ShieldCheck,
  Square,
  Workflow,
} from "lucide-react";
import { api, post, wsPath } from "../api";
import {
  Badge,
  DateLabel,
  Duration,
  Empty,
  ErrorBox,
  PageHeader,
  useAction,
  useApp,
  useResource,
} from "../ui";
import type { Run, Trace, Workflow as WorkflowType } from "../types";

export default function RunsPage() {
  const { workspace } = useApp();
  const runs = useResource<Run[]>(wsPath(workspace.id, "/runs"), 2500);
  const workflows = useResource<WorkflowType[]>(
    wsPath(workspace.id, "/workflows"),
  );
  const [selected, setSelected] = useState<string | null>(() => {
    const id = sessionStorage.getItem("inspectRun");
    sessionStorage.removeItem("inspectRun");
    return id;
  });
  const detail = useResource<Run>(
    selected ? wsPath(workspace.id, `/runs/${selected}`) : null,
    2500,
  );
  const traces = useResource<Trace[]>(
    selected ? wsPath(workspace.id, `/runs/${selected}/traces`) : null,
    2500,
  );
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const { busy, act } = useAction();
  const data = runs.data || [];
  const complete = data.filter((r) => r.status === "completed");
  const median = complete.length
    ? complete.map((r) => r.latency_ms || 0).sort((a, b) => a - b)[
        Math.floor(complete.length / 2)
      ]
    : null;
  const filtered = data.filter(
    (r) =>
      (filter === "all" || r.status === filter) &&
      `${r.workflow_name} ${r.question}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const run = detail.data;
  if (selected && run)
    return (
      <>
        <div className="back-heading">
          <button className="text-button" onClick={() => setSelected(null)}>
            <ArrowLeft size={16} /> All runs
          </button>
          <span className="mono subtle">{run.id.slice(0, 8)}</span>
        </div>
        <PageHeader
          title={run.workflow_name}
          description={`Version ${run.version_number} · ${run.model_profile.chat_model}`}
        >
          <Badge value={run.status} />
          {["queued", "running"].includes(run.status) ? (
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await post(wsPath(workspace.id, `/runs/${run.id}/cancel`));
                  detail.reload();
                })
              }
            >
              <Square size={15} /> Cancel run
            </button>
          ) : (
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const workflow = workflows.data?.find((w) =>
                    w.versions.some((v) => v.id === run.version_id),
                  );
                  if (!workflow) throw new Error("Workflow not found");
                  const next = await post<Run>(
                    wsPath(workspace.id, `/workflows/${workflow.id}/runs`),
                    {
                      question: run.question,
                      version_id: run.version_id,
                      preview: true,
                      parent_run_id: run.id,
                    },
                  );
                  setSelected(next.id);
                  runs.reload();
                }, "Started a new linked run.")
              }
            >
              <Play size={15} /> Re-run
            </button>
          )}
        </PageHeader>
        <ErrorBox message={detail.error || traces.error || run.error} />
        <div className="run-summary-grid">
          <div className="panel question-panel">
            <div className="eyebrow">INPUT QUESTION</div>
            <p>{run.question}</p>
          </div>
          <div className="panel run-metric">
            <Clock3 size={18} />
            <strong>
              <Duration ms={run.latency_ms} />
            </strong>
            <span>End-to-end latency</span>
          </div>
          <div className="panel run-metric">
            <Workflow size={18} />
            <strong>{run.tokens.toLocaleString()}</strong>
            <span>Model tokens</span>
          </div>
        </div>
        <div className="run-detail-grid">
          <section className="panel trace-panel">
            <div className="panel-title">
              <h2>Execution timeline</h2>
              <span>{traces.data?.length || 0} steps</span>
            </div>
            <div className="trace-list">
              {traces.data?.map((trace, i) => (
                <details
                  className="trace-step"
                  key={trace.id}
                  open={trace.status === "failed"}
                >
                  <summary>
                    <span className={`trace-number ${trace.status}`}>
                      {trace.status === "completed" ? (
                        <Check size={15} />
                      ) : (
                        i + 1
                      )}
                    </span>
                    <div>
                      <strong>{trace.node_id}</strong>
                      <span>{trace.node_type}</span>
                    </div>
                    <Duration ms={trace.latency_ms} />
                    <Badge value={trace.status} />
                    <ChevronDown size={15} />
                  </summary>
                  <div className="trace-details">
                    <ErrorBox message={trace.error || undefined} />
                    <div className="trace-json">
                      <h4>Inputs</h4>
                      <pre>{JSON.stringify(trace.inputs, null, 2)}</pre>
                      <h4>Outputs</h4>
                      <pre>{JSON.stringify(trace.outputs, null, 2)}</pre>
                      {trace.tool_calls.length > 0 && (
                        <>
                          <h4>Tool calls</h4>
                          <pre>{JSON.stringify(trace.tool_calls, null, 2)}</pre>
                        </>
                      )}
                    </div>
                  </div>
                </details>
              ))}
              {!traces.data?.length && (
                <p className="muted padding">
                  Steps will appear as execution starts.
                </p>
              )}
            </div>
          </section>
          <aside className="panel result-panel">
            <div className="panel-title">
              <h2>Result</h2>
              <ShieldCheck size={17} />
            </div>
            {run.content_revoked ? (
              <ErrorBox message="Source content has been revoked." />
            ) : run.answer ? (
              <>
                <p>{run.answer.text}</p>
                <div className="result-sources">
                  <h4>Evidence</h4>
                  {run.answer.citations.map((c, i) => (
                    <div key={i}>
                      <span>{i + 1}</span>
                      <div>
                        <strong>
                          {
                            run.sources.find((s) => s.chunk_id === c.chunk_id)
                              ?.document_name
                          }
                        </strong>
                        <blockquote>{c.quote}</blockquote>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="muted">
                The answer will appear when the run completes.
              </p>
            )}
          </aside>
        </div>
      </>
    );
  return (
    <>
      <PageHeader
        eyebrow="FOLLOW EVERY STEP"
        title="Nothing behind the curtain"
        description="See what ran, what it found, and where you can make it better."
      />
      <ErrorBox message={runs.error} />
      <div className="stat-grid">
        <div className="stat-card">
          <span className="stat-icon mint">
            <Play size={20} />
          </span>
          <div>
            <span>Recent runs</span>
            <strong>
              {data.length}
              <small>last 100</small>
            </strong>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon blue">
            <Check size={20} />
          </span>
          <div>
            <span>Completed successfully</span>
            <strong>
              {data.length
                ? Math.round((complete.length / data.length) * 100) + "%"
                : "—"}
              <small>{complete.length} completed</small>
            </strong>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon cream">
            <Clock3 size={20} />
          </span>
          <div>
            <span>Median latency</span>
            <strong>
              <Duration ms={median} />
              <small>completed runs</small>
            </strong>
          </div>
        </div>
      </div>
      <section className="panel">
        <div className="panel-toolbar">
          <div className="panel-tabs">
            {["all", "completed", "running", "failed"].map((status) => (
              <button
                key={status}
                className={filter === status ? "active" : ""}
                onClick={() => setFilter(status)}
              >
                {status === "all" ? "All runs" : status}
              </button>
            ))}
          </div>
          <label className="search-field">
            <Search size={16} />
            <input
              aria-label="Search runs"
              placeholder="Search runs…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
        {filtered.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Workflow / question</th>
                  <th>Status</th>
                  <th>Latency</th>
                  <th>Tokens</th>
                  <th>Date</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((run) => (
                  <tr
                    key={run.id}
                    className="clickable"
                    onClick={() => setSelected(run.id)}
                  >
                    <td>
                      <strong>{run.workflow_name}</strong>
                      <span className="table-secondary ellipsis">
                        {run.question}
                      </span>
                    </td>
                    <td>
                      <Badge value={run.status} />
                    </td>
                    <td className="mono">
                      <Duration ms={run.latency_ms} />
                    </td>
                    <td className="mono muted">
                      {run.tokens.toLocaleString()}
                    </td>
                    <td className="muted">
                      <DateLabel value={run.created_at} />
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`Inspect run ${run.id.slice(0, 8)}`}
                      >
                        <ArrowRight size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={<Play size={28} />}
            title="Your workflows leave a trail"
            description="Preview a workflow or ask a published assistant a question to see its execution here."
          />
        )}
      </section>
    </>
  );
}
