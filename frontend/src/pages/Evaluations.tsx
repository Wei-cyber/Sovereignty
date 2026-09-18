import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Download,
  FileCheck2,
  FlaskConical,
  GitCompareArrows,
  MessageSquare,
  Play,
  Plus,
  Rocket,
  ShieldCheck,
  Target,
  ThumbsDown,
  ThumbsUp,
  Upload,
  X,
} from "lucide-react";
import { api, post, wsPath } from "../api";
import {
  Badge,
  DateLabel,
  Duration,
  Empty,
  ErrorBox,
  Modal,
  PageHeader,
  useAction,
  useApp,
  useResource,
} from "../ui";
import type { Document, Evaluation, Feedback, Suite, Workflow } from "../types";
import SuiteEditor from "./SuiteEditor";
import CalibrationPanel from "./CalibrationPanel";

const metricNames: Record<string, string> = {
  retrieval_recall: "Retrieval recall @5",
  answer_correctness: "Answer correctness",
  evidence_support: "Evidence support",
  abstention: "Correct abstention",
  citation_validity: "Valid citations",
  critical_failures: "Critical failures",
};
function percent(value: number | boolean | null | undefined) {
  return typeof value === "number" ? Math.round(value * 100) + "%" : "—";
}

export default function EvaluationsPage() {
  const { workspace, refresh, config, navigate, notify } = useApp();
  const workflows = useResource<Workflow[]>(
    wsPath(workspace.id, "/workflows"),
    4000,
  );
  const suites = useResource<Suite[]>(
    wsPath(workspace.id, "/evaluation-suites"),
    4000,
  );
  const reports = useResource<Evaluation[]>(
    wsPath(workspace.id, "/evaluations"),
    2000,
  );
  const feedback = useResource<Feedback[]>(
    wsPath(workspace.id, "/feedback"),
    5000,
  );
  const docs = useResource<Document[]>(wsPath(workspace.id, "/documents"));
  const [tab, setTab] = useState("evaluations");
  const [workflowId, setWorkflowId] = useState(
    sessionStorage.getItem("evaluateWorkflow") || "",
  );
  const [versionId, setVersionId] = useState(
    sessionStorage.getItem("evaluateVersion") || "",
  );
  const [suiteId, setSuiteId] = useState("");
  const [reportId, setReportId] = useState<string | null>(null);
  const [review, setReview] = useState<Suite | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [notes, setNotes] = useState("");
  const [editing, setEditing] = useState<Suite | "new" | null>(null);
  const [promoting, setPromoting] = useState<Feedback | null>(null);
  const [reference, setReference] = useState("");
  const [referenceDocs, setReferenceDocs] = useState<string[]>([]);
  const [abstention, setAbstention] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const { busy, act } = useAction();
  useEffect(() => {
    suites.reload();
    reports.reload();
    feedback.reload();
    workflows.reload();
  }, [refresh]);
  useEffect(() => {
    if (
      workflows.data?.length &&
      !workflows.data.some((w) => w.id === workflowId)
    )
      setWorkflowId(workflows.data[0].id);
  }, [workflows.data]);
  const workflow = workflows.data?.find((w) => w.id === workflowId);
  useEffect(() => {
    if (workflow && !workflow.versions.some((v) => v.id === versionId))
      setVersionId(workflow.versions[0].id);
  }, [workflow]);
  useEffect(() => {
    if (suites.data?.length && !suites.data.some((s) => s.id === suiteId))
      setSuiteId(suites.data[0].id);
  }, [suites.data]);
  const suite = suites.data?.find((s) => s.id === suiteId);
  const selectedReport = reports.data?.find((r) => r.id === reportId);
  const report =
    selectedReport || reports.data?.find((r) => r.version_id === versionId);
  const matchingReports =
    reports.data?.filter((r) =>
      workflow?.versions.some((v) => v.id === r.version_id),
    ) || [];
  const previous = matchingReports.find(
    (r) => r.id !== report?.id && r.status === "completed",
  );
  const version = workflow?.versions.find((v) => v.id === versionId);
  const openEditor = (item: Suite | "new") => setEditing(item);
  const exportReport = (r: Evaluation) => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(r, null, 2)], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `evaluation-${r.id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <>
      <PageHeader
        eyebrow="EVIDENCE BEFORE RELEASE"
        title="Confidence, measured"
        description="Know how your workflows perform before your team depends on them."
      >
        <button className="button" onClick={() => input.current?.click()}>
          <Upload size={16} /> Import suite
        </button>
        <button className="button primary" onClick={() => openEditor("new")}>
          <Plus size={17} /> Create suite
        </button>
      </PageHeader>
      <input
        ref={input}
        type="file"
        accept=".json"
        className="sr-only"
        aria-label="Import evaluation suite"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file)
            act(async () => {
              const body = JSON.parse(await file.text());
              const result = await post<Suite>(
                wsPath(workspace.id, "/evaluation-suites"),
                body,
              );
              setSuiteId(result.id);
              suites.reload();
              if (input.current) input.current.value = "";
            }, "Evaluation suite imported. Review it before running.");
        }}
      />
      <ErrorBox message={suites.error || reports.error || workflows.error} />
      <div className="page-tabs">
        <button
          className={tab === "evaluations" ? "active" : ""}
          onClick={() => setTab("evaluations")}
        >
          <FlaskConical size={16} /> Evaluations
        </button>
        <button
          className={tab === "suites" ? "active" : ""}
          onClick={() => setTab("suites")}
        >
          <FileCheck2 size={16} /> Test suites{" "}
          <span>{suites.data?.length || 0}</span>
        </button>
        <button
          className={tab === "feedback" ? "active" : ""}
          onClick={() => setTab("feedback")}
        >
          <MessageSquare size={16} /> Feedback{" "}
          <span>{feedback.data?.length || 0}</span>
        </button>
        <button
          className={tab === "calibration" ? "active" : ""}
          onClick={() => setTab("calibration")}
        >
          <ShieldCheck size={16} /> Grader check
        </button>
      </div>
      {tab === "calibration" && (
        <CalibrationPanel key={workspace.id} documents={docs.data || []} />
      )}
      {tab === "evaluations" && (
        <>
          <section className="panel evaluation-setup">
            <div>
              <label>
                Workflow
                <select
                  value={workflowId}
                  onChange={(e) => {
                    setWorkflowId(e.target.value);
                    setVersionId("");
                    setReportId(null);
                  }}
                >
                  {!workflows.data?.length && (
                    <option value="">Create a workflow first</option>
                  )}
                  {workflows.data?.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Version
                <select
                  value={versionId}
                  onChange={(e) => {
                    setVersionId(e.target.value);
                    setReportId(null);
                  }}
                >
                  {workflow?.versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      Version {v.number} · knowledge r{v.corpus_revision}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Evaluation suite
                <select
                  value={suiteId}
                  onChange={(e) => setSuiteId(e.target.value)}
                >
                  {!suites.data?.length && (
                    <option value="">Create a test suite first</option>
                  )}
                  {suites.data?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} · v{s.version}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button primary"
                disabled={busy || !version || !suite?.reviewed_at}
                onClick={() =>
                  act(async () => {
                    const result = await post<Evaluation>(
                      wsPath(workspace.id, "/evaluations"),
                      { version_id: versionId, suite_id: suiteId },
                    );
                    setReportId(result.id);
                    reports.reload();
                  }, "Evaluation queued. Each question will run through the saved workflow.")
                }
              >
                <Play size={16} /> Run evaluation
              </button>
            </div>
            <div className="evaluation-setup-note">
              <ShieldCheck size={15} />
              {suite && !suite.reviewed_at ? (
                <>
                  <span>{suite.cases.length} examples await human review.</span>
                  <button
                    className="text-button"
                    onClick={() => {
                      setReview(suite);
                      setReviewed(false);
                      setNotes("");
                    }}
                  >
                    Review examples
                    <ArrowRight size={14} />
                  </button>
                </>
              ) : (
                <span>
                  {suite
                    ? `${suite.cases.length} reviewed examples · Quality gates enforced before publication`
                    : "Create a suite with reference answers and expected source documents."}
                </span>
              )}
            </div>
          </section>
    
          <div className="metric-grid">
            {[
              "retrieval_recall",
              "answer_correctness",
              "evidence_support",
              "abstention",
            ].map((key) => (
              <div className="metric-card" key={key}>
                <span>{metricNames[key]}</span>
                <strong>{percent(report?.metrics[key])}</strong>
                <div>
                  <span>
                    Target ≥{Math.round((suite?.thresholds[key] || 0) * 100)}%
                  </span>
                  {previous &&
                    typeof previous.metrics[key] === "number" &&
                    typeof report?.metrics[key] === "number" && (
                      <span className="metric-delta">
                        {(
                          ((report.metrics[key] as number) -
                            (previous.metrics[key] as number)) *
                          100
                        ).toFixed(0)}{" "}
                        pts vs previous
                      </span>
                    )}
                </div>
                <div className="metric-track">
                  <i
                    style={{
                      width: `${typeof report?.metrics[key] === "number" ? Math.min(100, (report.metrics[key] as number) * 100) : 0}%`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
          <section className="panel">
            <div className="panel-title">
              <h2>Evaluation history</h2>
              <span>{matchingReports.length} runs</span>
            </div>
            {matchingReports.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Version</th>
                      <th>Status</th>
                      <th>Correctness</th>
                      <th>Latency p95</th>
                      <th>Date</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {matchingReports.map((r) => (
                      <tr
                        key={r.id}
                        className={`clickable ${report?.id === r.id ? "selected-row" : ""}`}
                        onClick={() => setReportId(r.id)}
                      >
                        <td>
                          <span className="mono">{r.id.slice(0, 8)}</span>
                          <span className="table-secondary">
                            {r.results.length} cases completed
                          </span>
                        </td>
                        <td>
                          v
                          {
                            workflow?.versions.find(
                              (v) => v.id === r.version_id,
                            )?.number
                          }
                        </td>
                        <td>
                          <Badge
                            value={
                              r.status === "completed"
                                ? r.passed
                                  ? "passed"
                                  : "blocked"
                                : r.status
                            }
                          />
                        </td>
                        <td className="mono">
                          {percent(r.metrics.answer_correctness)}
                        </td>
                        <td className="mono">
                          <Duration
                            ms={
                              typeof r.metrics.latency_p95_ms === "number"
                                ? r.metrics.latency_p95_ms
                                : null
                            }
                          />
                        </td>
                        <td className="muted">
                          <DateLabel value={r.created_at} />
                        </td>
                        <td>
                          <ArrowRight size={16} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                icon={<FlaskConical size={30} />}
                title="A good answer should hold up to a test"
                description="Review your test suite, then run an evaluation to see how this workflow performs."
              />
            )}
          </section>
          {report && (
            <section className="panel evaluation-detail">
              <div className="panel-title">
                <div>
                  <h2>Results · {report.id.slice(0, 8)}</h2>
                  <p>{report.results.length} completed examples</p>
                </div>
                <div className="header-actions">
                  <button
                    className="button small"
                    onClick={() => exportReport(report)}
                  >
                    <Download size={14} /> Export
                  </button>
                  <button
                    className="button primary small"
                    disabled={
                      busy || !report.passed || report.status !== "completed"
                    }
                    onClick={() =>
                      act(async () => {
                        await post(
                          wsPath(
                            workspace.id,
                            `/workflows/${workflow!.id}/publish`,
                          ),
                          {
                            version_id: report.version_id,
                            report_id: report.id,
                          },
                        );
                        workflows.reload();
                      }, "Approved version published to your workspace.")
                    }
                  >
                    <Rocket size={15} />
                    {workflow?.published_version_id === report.version_id
                      ? "Republish version"
                      : "Publish this version"}
                  </button>
                </div>
              </div>
              <ErrorBox message={report.error || undefined} />
              <div className="gate-summary">
                {Object.entries(metricNames).map(([key, label]) => {
                  const value = report.metrics[key];
                  const threshold =
                    suites.data?.find((s) => s.id === report.suite_id)
                      ?.thresholds[key] || 0;
                  const passed =
                    typeof value === "number" &&
                    (key === "critical_failures"
                      ? value === 0
                      : value >= threshold);
                  return (
                    <span key={key} className={passed ? "pass" : ""}>
                      {passed ? (
                        <CheckCircle2 size={14} />
                      ) : (
                        <Target size={14} />
                      )}{" "}
                      {label}{" "}
                      <strong>
                        {key === "critical_failures"
                          ? (value ?? "—")
                          : percent(value)}
                      </strong>
                    </span>
                  );
                })}
              </div>
              <div className="case-results">
                {report.results.map((r) => (
                  <details key={r.case_id}>
                    <summary>
                      <span
                        className={`case-dot ${r.status === "completed" && (r.answer_correctness ?? r.abstention ?? 0) >= 0.85 ? "pass" : "fail"}`}
                      />
                      <strong>{r.question}</strong>
                      <span>
                        {percent(r.answer_correctness ?? r.abstention)}
                      </span>
                      <ChevronDown size={14} />
                    </summary>
                    <div>
                      <p>{r.rationale}</p>
                      <button
                        className="text-button"
                        onClick={() => {
                          sessionStorage.setItem("inspectRun", r.run_id);
                          navigate("runs");
                        }}
                      >
                        Inspect execution
                        <ArrowRight size={14} />
                      </button>
                    </div>
                  </details>
                ))}
              </div>
            </section>
          )}
        </>
      )}
      {tab === "suites" && (
        <div className="suite-grid">
          {suites.data?.map((s) => (
            <article className="panel suite-card" key={s.id}>
              <div className="section-title">
                <span className="stat-icon mint">
                  <FileCheck2 size={22} />
                </span>
                <Badge value={s.reviewed_at ? "reviewed" : "draft"}>
                  {s.reviewed_at ? "Reviewed" : "Review required"}
                </Badge>
              </div>
              <h2>{s.name}</h2>
              <p>
                {s.cases.length} examples · Version {s.version}
              </p>
              <div className="suite-breakdown">
                <span>
                  {s.cases.filter((c) => !c.expected_abstention).length}{" "}
                  answerable
                </span>
                <span>
                  {s.cases.filter((c) => c.expected_abstention).length}{" "}
                  abstentions
                </span>
                <span>
                  {s.cases.filter((c) => c.category === "adversarial").length}{" "}
                  adversarial
                </span>
              </div>
              <div className="suite-actions">
                <button className="button small" onClick={() => openEditor(s)}>
                  Edit as new version
                </button>
                <button
                  className="button primary small"
                  onClick={() => {
                    setReview(s);
                    setReviewed(false);
                    setNotes("");
                  }}
                >
                  Review examples
                  <ArrowRight size={14} />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {tab === "feedback" && (
        <section className="panel">
          <div className="panel-title">
            <h2>Learn from your team</h2>
            <span>Feedback becomes reviewed regression tests</span>
          </div>
          {feedback.data?.length ? (
            feedback.data.map((f) => (
              <div className="feedback-row" key={f.id}>
                <span
                  className={`feedback-icon ${f.rating === 1 ? "positive" : ""}`}
                >
                  {f.rating === 1 ? (
                    <ThumbsUp size={18} />
                  ) : (
                    <ThumbsDown size={18} />
                  )}
                </span>
                <div>
                  <strong>{f.question}</strong>
                  <p>{f.comment || "No additional comment."}</p>
                </div>
                {f.promoted_suite_id ? (
                  <Badge value="reviewed">Added to test suite</Badge>
                ) : (
                  <button
                    className="button small"
                    onClick={() => {
                      setPromoting(f);
                      setReference("");
                      setReferenceDocs([]);
                      setAbstention(false);
                    }}
                  >
                    <Plus size={14} /> Add regression case
                  </button>
                )}
              </div>
            ))
          ) : (
            <Empty
              icon={<MessageSquare size={28} />}
              title="Make every answer a learning opportunity"
              description="Employee feedback will appear here. Review it and turn useful examples into regression tests."
            />
          )}
        </section>
      )}
      {review && (
        <Modal
          title={`Review ${review.name}`}
          close={() => setReview(null)}
          wide
        >
          <p className="muted">
            Check every question, reference answer, and source against your
            knowledge. This records your review; it does not automatically
            publish anything.
          </p>
          <div className="review-cases">
            {review.cases.map((c, i) => (
              <div key={c.id}>
                <div className="review-case-number">{i + 1}</div>
                <div>
                  <strong>{c.question}</strong>
                  <p>
                    {c.expected_abstention
                      ? "Expected: explicit abstention"
                      : c.reference_answer}
                  </p>
                  <small>
                    {c.source_document_ids
                      ?.map(
                        (id) => docs.data?.find((d) => d.id === id)?.name || id,
                      )
                      .join(" · ") || c.category}
                  </small>
                </div>
              </div>
            ))}
          </div>
          {review.reviewed_at ? (
            <div className="review-confirmed">
              <CheckCircle2 size={18} />
              <div>
                <strong>Review recorded</strong>
                <p>{review.review_notes}</p>
              </div>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                act(async () => {
                  await post(
                    wsPath(
                      workspace.id,
                      `/evaluation-suites/${review.id}/review`,
                    ),
                    { notes },
                  );
                  suites.reload();
                  setReview(null);
                }, "Review recorded. This suite can now be evaluated.");
              }}
            >
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={reviewed}
                  onChange={(e) => setReviewed(e.target.checked)}
                />
                I have reviewed every example and its expected evidence.
              </label>
              <label>
                Review notes
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Describe the checks you performed and any limitations."
                  minLength={10}
                  maxLength={4000}
                  required
                  rows={3}
                />
              </label>
              <div className="modal-actions">
                <button className="button primary" disabled={busy || !reviewed}>
                  <Check size={16} /> Record human review
                </button>
              </div>
            </form>
          )}
        </Modal>
      )}
      {editing && (
        <SuiteEditor
          item={editing}
          documents={docs.data || []}
          close={() => setEditing(null)}
          saved={(s) => {
            setSuiteId(s.id);
            setEditing(null);
            suites.reload();
          }}
        />
      )}
      {promoting && (
        <Modal title="Add a regression case" close={() => setPromoting(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await post(
                  wsPath(workspace.id, `/feedback/${promoting.id}/promote`),
                  {
                    suite_id: suiteId,
                    reference_answer: reference,
                    source_document_ids: referenceDocs,
                    expected_abstention: abstention,
                  },
                );
                setPromoting(null);
                feedback.reload();
                suites.reload();
              }, "Created a new suite version for review.");
            }}
          >
            <p>
              <strong>{promoting.question}</strong>
            </p>
            <label>
              Target suite
              <select
                value={suiteId}
                onChange={(e) => setSuiteId(e.target.value)}
              >
                {suites.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · v{s.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={abstention}
                onChange={(e) => setAbstention(e.target.checked)}
              />
              This question should produce an abstention.
            </label>
            {!abstention && (
              <>
                <label>
                  Verified reference answer
                  <textarea
                    rows={4}
                    value={reference}
                    onChange={(e) => setReference(e.target.value)}
                    required
                  />
                </label>
                <label>
                  Supporting sources
                  <select
                    multiple
                    value={referenceDocs}
                    onChange={(e) =>
                      setReferenceDocs(
                        Array.from(e.target.selectedOptions, (o) => o.value),
                      )
                    }
                  >
                    {docs.data
                      ?.filter((d) => d.status === "ready")
                      .map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                  </select>
                </label>
              </>
            )}
            <div className="modal-actions">
              <button className="button primary" disabled={busy || !suiteId}>
                Add case for review
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
