import { useState } from "react";
import { api, post, wsPath } from "../api";
import { useApp, useResource, useAction, ErrorBox, Modal, Badge } from "../ui";
import type { Workflow, Run, Document } from "../types";
export const toolLabels: Record<string, string> = {
  knowledge_search: "Search workspace documents",
  read_source: "Read workspace sources",
  web_search: "Search the public web",
  web_read: "Read public pages",
  gmail_search: "Search my Gmail",
  gmail_read: "Read my Gmail",
  drive_search: "Search my selected Drive files",
  drive_read: "Read my selected Drive files",
  gmail_save_draft: "Save an unsent Gmail draft (user action only)",
};
const blank = {
  name: "",
  description: "",
  instructions:
    "Answer using permitted evidence. Cite your sources and say when evidence is insufficient.",
  starters: [] as string[],
  tools: ["knowledge_search", "read_source"],
  knowledge_document_ids: [] as string[],
};
export default function AgentsPage() {
  const { workspace, navigate } = useApp();
  const list = useResource<Workflow[]>(wsPath(workspace.id, "/agents"));
  const docs = useResource<Document[]>(wsPath(workspace.id, "/documents"));
  const { busy, act } = useAction();
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState(blank);
  const [preview, setPreview] = useState<Workflow | null>(null);
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState("");
  const run = useResource<Run>(
    runId ? wsPath(workspace.id, `/runs/${runId}`) : null,
    1500,
  );
  const open = (item?: Workflow) => {
    setEditing(item?.id || "new");
    const node = item?.versions[0].definition.nodes.find(
      (n) => n.type === "agent",
    );
    setForm(
      item
        ? {
            name: item.name,
            description: item.description,
            instructions: node?.config?.prompt || blank.instructions,
            starters: item.starters || [],
            tools: node?.config?.tools || [],
            knowledge_document_ids: node?.config?.knowledge_document_ids || [],
          }
        : blank,
    );
  };
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Agents</h1>
          <p>
            Build an assistant for your work. Your agents and their
            conversations are private.
          </p>
        </div>
        <button className="button primary" onClick={() => open()}>
          Create agent
        </button>
      </div>
      <ErrorBox message={list.error} />
      <div className="agent-grid">
        {list.data?.map((item) => (
          <article className="agent-card" key={item.id}>
            <Badge value="draft">Not reviewed · Private</Badge>
            <h2>{item.name}</h2>
            <p>
              {item.description ||
                "A personal assistant using your permitted tools."}
            </p>
            <p className="muted">
              Version {item.versions[0]?.number} · {item.versions.length} saved
              versions
            </p>
            <div className="agent-actions">
              <button className="button" onClick={() => open(item)}>
                Edit
              </button>
              <button
                className="button"
                onClick={() => {
                  setPreview(item);
                  setRunId("");
                  setQuestion("");
                }}
              >
                Preview
              </button>
              <button
                className="button"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    await post(
                      wsPath(workspace.id, `/agents/${item.id}/duplicate`),
                    );
                    list.reload();
                  }, "Agent duplicated.")
                }
              >
                Duplicate
              </button>
              <button
                className="button"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    await post(
                      wsPath(workspace.id, `/agents/${item.id}/submit`),
                    );
                  }, "Submitted for administrator review in Workflows. Your private agent stays available.")
                }
              >
                Request sharing
              </button>
              <button
                className="button danger"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    await api(wsPath(workspace.id, `/agents/${item.id}`), {
                      method: "DELETE",
                    });
                    list.reload();
                  }, "Agent removed. Run history is retained.")
                }
              >
                Delete agent
              </button>
            </div>
          </article>
        ))}
      </div>
      {!list.loading && !list.data?.length && (
        <p className="empty-state">
          Create your first agent, choose its tools, then try it privately.
        </p>
      )}
      <p className="muted">
        When web search is enabled, your question is sent to public search
        engines. Private source text is never added to that search.
      </p>
      <p className="muted">
        Shared agents need administrator review and passing evaluations. They
        use each person's own Google connections.
      </p>
      <button className="button" onClick={() => navigate("connections")}>
        Manage my connections
      </button>
      {editing && (
        <Modal
          title={
            editing === "new" ? "Create agent" : "Save a new agent version"
          }
          close={() => setEditing(null)}
          wide
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await post(
                  wsPath(
                    workspace.id,
                    editing === "new"
                      ? "/agents"
                      : `/agents/${editing}/versions`,
                  ),
                  form,
                );
                list.reload();
                setEditing(null);
              }, "Agent saved. You can use it in Assistant immediately.");
            }}
          >
            <label>
              Name
              <input
                required
                maxLength={120}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label>
              Description
              <input
                maxLength={2000}
                value={form.description}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
              />
            </label>
            <label>
              Instructions
              <textarea
                required
                rows={5}
                maxLength={8000}
                value={form.instructions}
                onChange={(e) =>
                  setForm({ ...form, instructions: e.target.value })
                }
              />
            </label>
            <label>
              Conversation starters (one per line, up to six)
              <textarea
                rows={3}
                value={form.starters.join("\n")}
                onChange={(e) =>
                  setForm({
                    ...form,
                    starters: e.target.value.split("\n").slice(0, 6),
                  })
                }
              />
            </label>
            <fieldset>
              <legend>Permitted tools</legend>
              {Object.entries(toolLabels)
                .filter(([k]) => k !== "gmail_save_draft")
                .map(([key, label]) => (
                  <label className="check-row" key={key}>
                    <input
                      type="checkbox"
                      checked={form.tools.includes(key)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          tools: e.target.checked
                            ? [...form.tools, key]
                            : form.tools.filter((t) => t !== key),
                        })
                      }
                    />
                    {label}
                  </label>
                ))}
            </fieldset>
            <fieldset>
              <legend>Workspace knowledge</legend>
              <p className="muted">
                Leave all unchecked to use all permitted workspace documents.
                Drive files are selected separately in Connections.
              </p>
              {docs.data
                ?.filter((d) => d.status === "ready")
                .map((d) => (
                  <label className="check-row" key={d.id}>
                    <input
                      type="checkbox"
                      checked={form.knowledge_document_ids.includes(d.id)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          knowledge_document_ids: e.target.checked
                            ? [...form.knowledge_document_ids, d.id]
                            : form.knowledge_document_ids.filter(
                                (id) => id !== d.id,
                              ),
                        })
                      }
                    />
                    {d.name}
                  </label>
                ))}
            </fieldset>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                Save agent
              </button>
            </div>
          </form>
        </Modal>
      )}
      {preview && (
        <Modal
          title={`Preview ${preview.name}`}
          close={() => setPreview(null)}
          wide
        >
          <p className="muted">
            Not reviewed. This private preview uses your connections and live
            inference.
          </p>
          <label>
            Saved version
            <select
              value={preview.versions[0].id}
              onChange={(e) =>
                setPreview({
                  ...preview,
                  versions: [
                    preview.versions.find((v) => v.id === e.target.value)!,
                    ...preview.versions.filter((v) => v.id !== e.target.value),
                  ],
                })
              }
            >
              {preview.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  Version {v.number}
                </option>
              ))}
            </select>
          </label>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                const r = await post<Run>(
                  wsPath(workspace.id, `/workflows/${preview.id}/runs`),
                  {
                    question,
                    version_id: preview.versions[0].id,
                    preview: true,
                  },
                );
                setRunId(r.id);
              });
            }}
          >
            <label>
              Try a question
              <textarea
                required
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
            </label>
            <button
              className="button primary"
              disabled={
                busy || ["running", "queued"].includes(run.data?.status || "")
              }
            >
              Run preview
            </button>
          </form>
          <ErrorBox message={run.error || run.data?.error} />
          {run.data && (
            <div className="agent-preview">
              <Badge value={run.data.status} />
              <p>{run.data.answer?.text}</p>
              {run.data.sources.map((s) => (
                <details key={s.chunk_id}>
                  <summary>
                    {s.document_name} · {s.location}
                  </summary>
                  <p>{s.text}</p>
                </details>
              ))}
              {["running", "queued"].includes(run.data.status) && (
                <button
                  className="button"
                  onClick={() =>
                    act(async () => {
                      await post(wsPath(workspace.id, `/runs/${runId}/cancel`));
                    })
                  }
                >
                  Stop preview
                </button>
              )}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
