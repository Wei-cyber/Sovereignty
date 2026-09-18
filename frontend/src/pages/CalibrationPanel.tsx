import { useState } from "react";
import { Plus, ShieldCheck } from "lucide-react";
import { api, post, wsPath } from "../api";
import { Badge, ErrorBox, Modal, useAction, useApp, useResource } from "../ui";
import type { components } from "../generated/api";
import type { Document } from "../types";

type Check = Omit<components["schemas"]["CalibrationView"], "examples"> & {
  examples: Example[];
};
type Example = Required<components["schemas"]["HumanExample"]>;
type Chunk = { id: string; text: string; location: string };
const blank = (): Example => ({
  question: "",
  reference_answer: "",
  answer_text: "",
  source_chunk_ids: [],
  human_correctness: null,
  human_evidence_support: null,
});
const ratingLabels = [
  "Incorrect / unsupported",
  "Mostly incorrect / unsupported",
  "Partly correct / supported",
  "Mostly correct / supported",
  "Fully correct / supported",
];

function PassagePicker({
  documents,
  value,
  onChange,
}: {
  documents: Document[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const { workspace } = useApp();
  const [documentId, setDocumentId] = useState("");
  const chunks = useResource<Chunk[]>(
    documentId ? wsPath(workspace.id, `/documents/${documentId}/chunks`) : null,
  );
  return (
    <fieldset className="source-choices">
      <legend>Supporting evidence</legend>
      <label>
        Choose a document
        <select
          value={documentId}
          onChange={(e) => setDocumentId(e.target.value)}
        >
          <option value="">Select a document to inspect its passages</option>
          {documents
            .filter((d) => d.status === "ready")
            .map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} · version {d.version}
              </option>
            ))}
        </select>
      </label>
      <ErrorBox message={chunks.error} />
      <p className="muted">
        {value.length} of 5 passages selected. Read the evidence before rating
        the answer.
      </p>
      {value.length > 0 && (
        <button
          type="button"
          className="text-button"
          onClick={() => onChange([])}
        >
          Clear selected passages
        </button>
      )}
      {chunks.data?.map((c) => (
        <label className="passage-choice" key={c.id}>
          <input
            type="checkbox"
            checked={value.includes(c.id)}
            disabled={!value.includes(c.id) && value.length >= 5}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...value, c.id]
                  : value.filter((id) => id !== c.id),
              )
            }
          />
          <span>
            <strong>{c.location}</strong>
            <span className="passage-text">{c.text}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function Editor({
  item,
  documents,
  close,
  saved,
}: {
  item: Check | null;
  documents: Document[];
  close: () => void;
  saved: (c: Check) => void;
}) {
  const { workspace } = useApp();
  const { act, busy } = useAction();
  const draft = item?.status === "draft";
  const [name, setName] = useState(
    item ? `${item.name}${draft ? "" : " · revision"}` : "",
  );
  const [examples, setExamples] = useState<Example[]>(
    item?.examples.length ? item.examples : [blank()],
  );
  const rated = examples.filter(
    (e) =>
      e.question.trim() &&
      e.reference_answer.trim() &&
      e.answer_text.trim() &&
      e.source_chunk_ids.length &&
      e.human_correctness != null &&
      e.human_evidence_support != null,
  ).length;
  const update = (index: number, patch: Partial<Example>) =>
    setExamples(examples.map((e, i) => (i === index ? { ...e, ...patch } : e)));
  return (
    <Modal
      title={draft ? "Edit grader examples" : "Prepare grader examples"}
      close={close}
      wide
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          act(async () => {
            const body = { name: name.trim(), examples };
            const result = draft
              ? await api<Check>(
                  wsPath(workspace.id, `/grader-calibrations/${item!.id}`),
                  { method: "PUT", body: JSON.stringify(body) },
                )
              : await post<Check>(
                  wsPath(workspace.id, "/grader-calibrations"),
                  body,
                );
            saved(result);
          }, "Draft saved. Your ratings have not been submitted for checking yet.");
        }}
      >
        <p>
          Check that the AI grader agrees with your judgment. Supply at least 10
          different question/answer examples, including both poor and strong
          answers. You can use answers from Runs or write sample answers
          yourself.
        </p>
        <label>
          Check name
          <input
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="For example, Policy answer grading"
          />
        </label>
        <p role="status">
          <strong>
            {rated} / {examples.length}
          </strong>{" "}
          examples complete. Drafts can be saved at any time.
        </p>
        {examples.map((example, index) => (
          <details
            className="setup-example"
            key={index}
            open={examples.length === 1 || undefined}
          >
            <summary>
              Example {index + 1} · {example.question || "New question"}
            </summary>
            <label>
              Question
              <input
                aria-label={`Grader question ${index + 1}`}
                maxLength={8000}
                value={example.question}
                onChange={(e) => update(index, { question: e.target.value })}
              />
            </label>
            <PassagePicker
              documents={documents}
              value={example.source_chunk_ids}
              onChange={(ids) => update(index, { source_chunk_ids: ids })}
            />
            <label>
              Verified correct answer
              <textarea
                rows={3}
                maxLength={8000}
                value={example.reference_answer}
                onChange={(e) =>
                  update(index, { reference_answer: e.target.value })
                }
                placeholder="Write the answer supported by the selected passages."
              />
            </label>
            <label>
              Sample answer to grade
              <textarea
                rows={3}
                maxLength={8000}
                value={example.answer_text}
                onChange={(e) => update(index, { answer_text: e.target.value })}
                placeholder="Paste or write a sample answer. Include good and bad examples across your set."
              />
            </label>
            <div className="rating-grid">
              {(["human_correctness", "human_evidence_support"] as const).map(
                (field) => (
                  <label key={field}>
                    {field === "human_correctness"
                      ? "Your correctness rating"
                      : "Your evidence-support rating"}
                    <select
                      value={
                        example[field] == null ? "" : String(example[field])
                      }
                      onChange={(e) =>
                        update(index, {
                          [field]:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        })
                      }
                    >
                      <option value="">Choose your rating</option>
                      {ratingLabels.map((label, i) => (
                        <option key={i} value={i / 4}>
                          {i * 25}% ·{" "}
                          {
                            label.split(" / ")[
                              field === "human_correctness" ? 0 : 1
                            ]
                          }
                        </option>
                      ))}
                    </select>
                  </label>
                ),
              )}
            </div>
            <button
              type="button"
              className="text-button"
              disabled={examples.length === 1}
              onClick={() =>
                setExamples(examples.filter((_, i) => i !== index))
              }
            >
              Remove example {index + 1}
            </button>
          </details>
        ))}
        <button
          type="button"
          className="button"
          disabled={examples.length >= 50}
          onClick={() => setExamples([...examples, blank()])}
        >
          <Plus size={16} /> Add example
        </button>
        <div className="modal-actions">
          <button type="button" className="button" onClick={close}>
            Cancel
          </button>
          <button className="button primary" disabled={busy || !name.trim()}>
            Save draft
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function CalibrationPanel({
  documents,
}: {
  documents: Document[];
}) {
  const { workspace, config } = useApp();
  const checks = useResource<Check[]>(
    wsPath(workspace.id, "/grader-calibrations"),
    2000,
  );
  const { act, busy } = useAction();
  const [editing, setEditing] = useState<Check | "new" | null>(null);
  const [review, setReview] = useState<Check | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [notes, setNotes] = useState("");
  const active = checks.data?.find((c) => c.active && c.eligible);
  return (
    <section className="panel calibration-panel">
      <div className="section-title">
        <div>
          <h2>Check the AI grader</h2>
          <p className="muted">
            Compare its scores with your human ratings before trusting automated
            evaluations.
          </p>
        </div>
        <button className="button primary" onClick={() => setEditing("new")}>
          <Plus size={16} /> Prepare examples
        </button>
      </div>
      <div className="setup-hint">
        <strong>
          {active ? `Using ${active.name}` : "No active in-app grader check"}
        </strong>
        <p>
          1. Prepare and rate examples. 2. Review and run the check. 3. Use a
          passing result for this workspace. Then save and evaluate a new
          workflow version.
        </p>
      </div>

      <ErrorBox message={checks.error} />
      {!checks.data?.length && (
        <p>
          Begin with ten examples from documents you understand. Include at
          least one poor (0–25%) and one strong (75–100%) answer for each
          rating. Ratings are always entered by you.
        </p>
      )}
      {checks.data?.map((c) => {
        const report = c.report as {
          passed?: boolean;
          metrics?: { correctness_mae: number; evidence_support_mae: number };
          results?: {
            index: number;
            human_correctness: number;
            human_evidence_support: number;
            graded_correctness: number;
            graded_evidence_support: number;
          }[];
        };
        const running = ["queued", "running"].includes(c.status);
        return (
          <article className="setup-example" key={c.id}>
            <div className="section-title">
              <h3>{c.name}</h3>
              <Badge value={c.status} />
            </div>
            {c.source_unavailable ? (
              <p role="alert">
                Supporting evidence was removed. This check is unavailable and
                cannot authorize publication.
              </p>
            ) : (
              <>
                <p>
                  {c.examples.length} human examples
                  {c.active && c.eligible ? " · Active for this workspace" : ""}
                </p>
                {running && (
                  <div role="status">
                    <progress
                      value={c.completed_examples}
                      max={c.examples.length || 1}
                    />{" "}
                    {c.completed_examples} / {c.examples.length} answers
                    checked. You can leave this page and return.
                  </div>
                )}
                <ErrorBox message={c.error} />
                {report.metrics && (
                  <>
                    <p>
                      <strong>
                        {report.passed
                          ? "Grader agrees closely enough"
                          : "Grader needs another review"}
                      </strong>
                    </p>
                    <p>
                      Average difference from your ratings: correctness{" "}
                      {(report.metrics.correctness_mae * 100).toFixed(1)}{" "}
                      percentage points; evidence support{" "}
                      {(report.metrics.evidence_support_mae * 100).toFixed(1)}.
                      Each must be at most 10.
                    </p>
                    {!report.passed && (
                      <p>
                        Inspect disagreements below, check your labels against
                        the evidence, and revise the examples if needed. Do not
                        change accurate human ratings just to make the grader
                        pass.
                      </p>
                    )}
                    <details>
                      <summary>Compare human and AI scores</summary>
                      <div className="table-scroll">
                        <table>
                          <thead>
                            <tr>
                              <th>Example</th>
                              <th>Your correctness</th>
                              <th>AI correctness</th>
                              <th>Your support</th>
                              <th>AI support</th>
                            </tr>
                          </thead>
                          <tbody>
                            {report.results?.map((r) => (
                              <tr key={r.index}>
                                <td>{c.examples[r.index]?.question}</td>
                                {[
                                  r.human_correctness,
                                  r.graded_correctness,
                                  r.human_evidence_support,
                                  r.graded_evidence_support,
                                ].map((s, i) => (
                                  <td key={i}>{Math.round(s * 100)}%</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  </>
                )}
                {c.status === "completed" && report.passed && !c.eligible && (
                  <p role="alert">
                    The grader configuration has changed. Prepare a revised
                    check before using this result.
                  </p>
                )}
                <div className="setup-actions">
                  {running && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        act(async () => {
                          await post(
                            wsPath(
                              workspace.id,
                              `/grader-calibrations/${c.id}/cancel`,
                            ),
                          );
                          checks.reload();
                        }, "Check cancelled. Any model request already in progress may finish.")
                      }
                    >
                      Cancel check
                    </button>
                  )}
                  {!running && (
                    <button className="button" onClick={() => setEditing(c)}>
                      {c.status === "draft"
                        ? "Edit examples"
                        : "Revise examples"}
                    </button>
                  )}
                  {c.status === "draft" && (
                    <button
                      className="button primary"
                      onClick={() => {
                        setReview(c);
                        setConfirmed(false);
                        setNotes("");
                      }}
                    >
                      Review and check grader
                    </button>
                  )}
                  {c.eligible && !c.active && (
                    <button
                      className="button primary"
                      disabled={busy}
                      onClick={() =>
                        act(async () => {
                          await post(
                            wsPath(
                              workspace.id,
                              `/grader-calibrations/${c.id}/activate`,
                            ),
                          );
                          checks.reload();
                        }, "Grader check activated. Save and evaluate a new workflow version before publishing.")
                      }
                    >
                      <ShieldCheck size={16} /> Use for this workspace
                    </button>
                  )}
                </div>
              </>
            )}
          </article>
        );
      })}
      {editing && (
        <Editor
          item={editing === "new" ? null : editing}
          documents={documents}
          close={() => setEditing(null)}
          saved={() => {
            setEditing(null);
            checks.reload();
          }}
        />
      )}
      {review && (
        <Modal
          title="Review your grader examples"
          close={() => setReview(null)}
          wide
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await post(
                  wsPath(
                    workspace.id,
                    `/grader-calibrations/${review.id}/start`,
                  ),
                  { confirmed, notes },
                );
                setReview(null);
                checks.reload();
              }, "Grader check queued. Results will appear here.");
            }}
          >
            <p>
              This will compare the configured grader with your ratings on{" "}
              {review.examples.length} examples. Live checks use the configured
              model and incur API charges.
            </p>
            {review.examples.map((e, i) => (
              <details className="setup-example" key={i}>
                <summary>
                  Example {i + 1}: {e.question || "Missing question"}
                </summary>
                <p>
                  <strong>Expected:</strong> {e.reference_answer}
                </p>
                <p>
                  <strong>Sample:</strong> {e.answer_text}
                </p>
                <p>
                  Your correctness:{" "}
                  {e.human_correctness == null
                    ? "Not rated"
                    : `${e.human_correctness * 100}%`}
                  . Your support:{" "}
                  {e.human_evidence_support == null
                    ? "Not rated"
                    : `${e.human_evidence_support * 100}%`}
                  .
                </p>
                <p>
                  {e.source_chunk_ids.length} supporting passages. Use Edit
                  examples to inspect or change them.
                </p>
              </details>
            ))}
            <label className="checkbox-row">
              <input
                type="checkbox"
                required
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              I reviewed the evidence and entered these ratings myself.
            </label>
            <label>
              Review notes
              <textarea
                required
                minLength={10}
                maxLength={4000}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Describe what you checked and any limitations of these examples."
              />
            </label>
            <div className="modal-actions">
              <button
                type="button"
                className="button"
                onClick={() => setReview(null)}
              >
                Back
              </button>
              <button
                className="button primary"
                disabled={busy || !confirmed || notes.trim().length < 10}
              >
                Check grader
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}
