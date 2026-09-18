import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { post, wsPath } from "../api";
import { Modal, useAction, useApp } from "../ui";
import type { Document, EvalCase, Suite } from "../types";

const defaults = {
  retrieval_recall: 0.85,
  answer_correctness: 0.85,
  evidence_support: 0.9,
  abstention: 0.9,
  citation_validity: 1,
  critical_failures: 0 as const,
};
const labels: Record<string, string> = {
  retrieval_recall: "Finding the expected sources",
  answer_correctness: "Answer correctness",
  evidence_support: "Answers supported by evidence",
  abstention: "Correctly declining unsupported questions",
  citation_validity: "Valid citations",
};
type Case = EvalCase & { source_document_ids: string[] };
const blank = (): Case => ({
  id: crypto.randomUUID(),
  fixture_version: "",
  question: "",
  reference_answer: "",
  source_document_ids: [],
  expected_abstention: false,
  category: "answerable",
});

export default function SuiteEditor({
  item,
  documents,
  close,
  saved,
}: {
  item: Suite | "new";
  documents: Document[];
  close: () => void;
  saved: (suite: Suite) => void;
}) {
  const { workspace } = useApp();
  const { busy, act } = useAction();
  const [name, setName] = useState(item === "new" ? "" : item.name);
  const [cases, setCases] = useState<Case[]>(
    item === "new"
      ? [blank()]
      : item.cases.map((c) => ({
          ...c,
          source_document_ids: c.source_document_ids ?? [],
        })),
  );
  const [thresholds, setThresholds] = useState(
    item === "new" ? defaults : item.thresholds,
  );
  const [error, setError] = useState("");
  const update = (index: number, patch: Partial<Case>) =>
    setCases(cases.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const ready = documents.filter((d) => d.status === "ready");
  return (
    <Modal
      title={
        item === "new" ? "Create evaluation suite" : "Revise evaluation suite"
      }
      close={close}
      wide
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (
            cases.some(
              (c) =>
                !c.question.trim() ||
                (!c.expected_abstention &&
                  (!c.reference_answer.trim() ||
                    !c.source_document_ids.length)),
            )
          ) {
            setError(
              "Complete each question. Questions with an answer need an expected answer and a supporting document.",
            );
            return;
          }
          if (
            cases.some((c) =>
              c.source_document_ids.some(
                (id) => !ready.some((d) => d.id === id),
              ),
            )
          ) {
            setError(
              "A selected document is unavailable. Replace it before saving.",
            );
            return;
          }
          act(async () => {
            const suite = await post<Suite>(
              wsPath(
                workspace.id,
                item === "new"
                  ? "/evaluation-suites"
                  : `/evaluation-suites/${item.id}/versions`,
              ),
              { name: name.trim(), cases, thresholds },
            );
            saved(suite);
          }, "Suite saved. Review the examples before evaluating.");
        }}
      >
        <p className="muted">
          Write questions your team will ask and verify the expected answers
          against your documents. Saving a revision preserves earlier reviews
          and results.
        </p>
        <label>
          Suite name
          <input
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="For example, Employee policy checks"
          />
        </label>
        <div className="setup-examples">
          {cases.map((c, index) => (
            <fieldset className="setup-example" key={c.id}>
              <legend>Question {index + 1}</legend>
              <label>
                Question
                <input
                  aria-label={`Question ${index + 1}`}
                  required
                  maxLength={8000}
                  value={c.question}
                  onChange={(e) => update(index, { question: e.target.value })}
                />
              </label>
              <label>
                Expected behavior
                <select
                  aria-label={`Expected behavior ${index + 1}`}
                  value={c.expected_abstention ? "decline" : "answer"}
                  onChange={(e) =>
                    update(index, {
                      expected_abstention: e.target.value === "decline",
                      category:
                        e.target.value === "decline"
                          ? "unanswerable"
                          : "answerable",
                      source_document_ids:
                        e.target.value === "decline"
                          ? []
                          : c.source_document_ids,
                      reference_answer:
                        e.target.value === "decline" ? "" : c.reference_answer,
                    })
                  }
                >
                  <option value="answer">Answer using the documents</option>
                  <option value="decline">
                    Say there is not enough evidence
                  </option>
                </select>
              </label>
              {!c.expected_abstention && (
                <>
                  <label>
                    Expected answer
                    <textarea
                      aria-label={`Expected answer ${index + 1}`}
                      required
                      rows={3}
                      maxLength={8000}
                      value={c.reference_answer}
                      onChange={(e) =>
                        update(index, { reference_answer: e.target.value })
                      }
                    />
                  </label>
                  <fieldset className="source-choices">
                    <legend>Supporting documents</legend>
                    {!ready.length && (
                      <p>
                        Upload a document in Knowledge and wait until it is
                        ready.
                      </p>
                    )}
                    {ready.map((d) => (
                      <label className="checkbox-row" key={d.id}>
                        <input
                          type="checkbox"
                          checked={c.source_document_ids.includes(d.id)}
                          onChange={(e) =>
                            update(index, {
                              source_document_ids: e.target.checked
                                ? [...c.source_document_ids, d.id]
                                : c.source_document_ids.filter(
                                    (id) => id !== d.id,
                                  ),
                            })
                          }
                        />
                        {d.name} · version {d.version}
                        {d.superseded_revision ? " · earlier version" : ""}
                      </label>
                    ))}
                    {c.source_document_ids.some(
                      (id) => !ready.some((d) => d.id === id),
                    ) && (
                      <p role="alert">
                        A previously selected document is unavailable.{" "}
                        <button
                          type="button"
                          className="text-button"
                          onClick={() =>
                            update(index, {
                              source_document_ids: c.source_document_ids.filter(
                                (id) => ready.some((d) => d.id === id),
                              ),
                            })
                          }
                        >
                          Remove unavailable selections
                        </button>
                      </p>
                    )}
                  </fieldset>
                </>
              )}
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={c.category === "adversarial"}
                  onChange={(e) =>
                    update(index, {
                      category: e.target.checked
                        ? "adversarial"
                        : c.expected_abstention
                          ? "unanswerable"
                          : "answerable",
                    })
                  }
                />
                This question tests misleading or malicious instructions
              </label>
              <button
                type="button"
                className="text-button"
                disabled={cases.length === 1}
                onClick={() => setCases(cases.filter((_, i) => i !== index))}
                aria-label={`Remove question ${index + 1}`}
              >
                <Trash2 size={14} /> Remove question
              </button>
            </fieldset>
          ))}
        </div>
        <button
          type="button"
          className="button"
          disabled={cases.length >= 500}
          onClick={() => setCases([...cases, blank()])}
        >
          <Plus size={16} /> Add question
        </button>
        {(!cases.some((c) => c.expected_abstention) ||
          !cases.some((c) => !c.expected_abstention)) && (
          <p className="setup-hint">
            Before publication, include both answerable questions and questions
            the assistant should decline.
          </p>
        )}
        <details className="setup-thresholds">
          <summary>Quality thresholds · recommended defaults included</summary>
          {Object.entries(labels).map(([key, label]) => (
            <label key={key}>
              {label} (%)
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                required
                value={Math.round(
                  Number(thresholds[key as keyof typeof thresholds]) * 100,
                )}
                onChange={(e) =>
                  setThresholds({
                    ...thresholds,
                    [key]: Number(e.target.value) / 100,
                  })
                }
              />
            </label>
          ))}
          <p>
            Critical authorization or tool-permission failures allowed:{" "}
            <strong>0</strong>.
          </p>
        </details>
        {error && (
          <p role="alert" className="setup-error">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button" onClick={close}>
            Cancel
          </button>
          <button className="button primary" disabled={busy || !name.trim()}>
            Save suite
          </button>
        </div>
      </form>
    </Modal>
  );
}
