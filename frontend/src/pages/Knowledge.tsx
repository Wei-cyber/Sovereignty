import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  FileText,
  FolderOpen,
  History,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, post, wsPath } from "../api";
import {
  Badge,
  DateLabel,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageHeader,
  useAction,
  useApp,
  useResource,
} from "../ui";
import type { Document } from "../types";

export default function KnowledgePage() {
  const { workspace, refresh, config } = useApp();
  const { busy, act } = useAction();
  const docs = useResource<Document[]>(
    wsPath(workspace.id, "/documents"),
    3000,
  );
  const [query, setQuery] = useState("");
  const [showVersions, setShowVersions] = useState(false);
  const [view, setView] = useState<Document | null>(null);
  const [deleting, setDeleting] = useState<Document | null>(null);
  const [replacing, setReplacing] = useState<Document | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const chunks = useResource<
    { id: string; text: string; location: string; ordinal: number }[]
  >(view ? wsPath(workspace.id, `/documents/${view.id}/chunks`) : null);
  useEffect(() => docs.reload(), [refresh]);
  const current = (docs.data || []).filter(
    (d) => showVersions || !d.superseded_revision,
  );
  const filtered = current.filter((d) =>
    d.name.toLowerCase().includes(query.toLowerCase()),
  );
  const ready = current.filter((d) => d.status === "ready").length;
  const admin = workspace.role === "admin";
  const upload = (files: FileList | File[]) =>
    act(async () => {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        if (replacing) form.append("replaces", replacing.id);
        await api(wsPath(workspace.id, "/documents"), {
          method: "POST",
          body: form,
        });
        if (replacing) break;
      }
      setReplacing(null);
      if (input.current) input.current.value = "";
      docs.reload();
    }, "Documents uploaded. Indexing will begin shortly.");
  return (
    <>
      <PageHeader
        eyebrow="THE FOUNDATION FOR BETTER ANSWERS"
        title="Your team's knowledge"
        description="Give your workflows the context they need. Keep every answer connected to a source."
      >
        {admin && (
          <button
            className="button primary"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            <Plus size={17} /> Add documents
          </button>
        )}
      </PageHeader>
      <input
        ref={input}
        className="sr-only"
        aria-label="Upload knowledge documents"
        type="file"
        accept=".pdf,.txt,.md"
        multiple={!replacing}
        onChange={(e) => {
          if (e.target.files?.length) upload(e.target.files);
        }}
      />
      <ErrorBox message={docs.error} />
      <div className="stat-grid">
        <div className="stat-card">
          <span className="stat-icon mint">
            <BookOpen size={20} />
          </span>
          <div>
            <span>Knowledge sources</span>
            <strong>
              {current.length}
              <small>documents</small>
            </strong>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon cream">
            <CheckCircle2 size={20} />
          </span>
          <div>
            <span>Ready for retrieval</span>
            <strong>
              {ready}
              <small>indexed</small>
            </strong>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-icon blue">
            <ShieldCheck size={20} />
          </span>
          <div>
            <span>Access boundary</span>
            <strong className="stat-word">
              Workspace<small>members only</small>
            </strong>
          </div>
        </div>
      </div>
      {admin && (
        <div
          className={`upload-zone ${dragging ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!busy) upload(e.dataTransfer.files);
          }}
        >
          <span className="upload-icon">
            <Upload size={23} />
          </span>
          <div>
            <h3>
              {replacing
                ? `Replace ${replacing.name}`
                : "A home for your company knowledge"}
            </h3>
            <p>
              Drop files here, or{" "}
              <button onClick={() => input.current?.click()} disabled={busy}>
                browse your files
              </button>
              . PDF, Markdown, and text · up to {config.max_upload_mb} MB each.
            </p>
          </div>
          {replacing && (
            <button
              className="icon-button"
              aria-label="Cancel replacement"
              onClick={() => setReplacing(null)}
            >
              <X size={17} />
            </button>
          )}
          <span className="upload-illustration" aria-hidden="true">
            <FileText size={32} />
            <FileText size={32} />
          </span>
        </div>
      )}
      <section className="panel">
        <div className="panel-toolbar">
          <div className="panel-tabs">
            <span className="active">
              All documents <b>{current.length}</b>
            </span>
          </div>
          <div className="toolbar-right">
            <label className="search-field">
              <Search size={16} />
              <input
                aria-label="Search documents"
                placeholder="Search documents…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button
              className={`button small ${showVersions ? "selected" : ""}`}
              onClick={() => setShowVersions((v) => !v)}
            >
              <History size={15} />
              {showVersions ? "Hide versions" : "Versions"}
            </button>
          </div>
        </div>
        {docs.loading && !docs.data ? (
          <Loading />
        ) : filtered.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Document name</th>
                  <th>Status</th>
                  <th>Chunks</th>
                  <th>Added</th>
                  <th className="align-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((doc) => (
                  <tr key={doc.id}>
                    <td>
                      <button
                        className="document-name"
                        onClick={() => setView(doc)}
                      >
                        <span
                          className={`file-icon ${doc.name.endsWith(".pdf") ? "pdf" : ""}`}
                        >
                          <FileText size={19} />
                        </span>
                        <div>
                          <strong>{doc.name}</strong>
                          <small>
                            {Math.max(1, Math.round(doc.size / 1024))} KB{" "}
                            <span>·</span> Version {doc.version}
                            {doc.superseded_revision
                              ? " · Previous version"
                              : ""}
                          </small>
                        </div>
                      </button>
                    </td>
                    <td>
                      <Badge value={doc.status} />
                      {doc.error && (
                        <span className="table-error" title={doc.error}>
                          {doc.error}
                        </span>
                      )}
                    </td>
                    <td className="mono">{doc.chunk_count || "—"}</td>
                    <td className="muted">
                      <DateLabel value={doc.created_at} />
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="icon-button"
                          title="View indexed content"
                          aria-label={`View ${doc.name}`}
                          onClick={() => setView(doc)}
                        >
                          <ArrowUpRight size={17} />
                        </button>
                        {admin && (
                          <>
                            {doc.status === "failed" ? (
                              <button
                                className="icon-button"
                                aria-label={`Retry ${doc.name}`}
                                title="Retry ingestion"
                                disabled={busy}
                                onClick={() =>
                                  act(async () => {
                                    await post(
                                      wsPath(
                                        workspace.id,
                                        `/documents/${doc.id}/retry`,
                                      ),
                                    );
                                    docs.reload();
                                  })
                                }
                              >
                                <RefreshCw size={16} />
                              </button>
                            ) : (
                              <button
                                className="icon-button"
                                title="Upload a new version"
                                aria-label={`Replace ${doc.name}`}
                                onClick={() => {
                                  setReplacing(doc);
                                  setTimeout(() => input.current?.click(), 0);
                                }}
                              >
                                <History size={16} />
                              </button>
                            )}
                            <button
                              className="icon-button danger"
                              title="Revoke document"
                              aria-label={`Delete ${doc.name}`}
                              onClick={() => setDeleting(doc)}
                            >
                              <Trash2 size={16} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={<FolderOpen size={30} />}
            title={
              query ? "No matching documents" : "Your knowledge starts here"
            }
            description={
              query
                ? "Try another search term."
                : "Upload your first document to give your workflows useful context."
            }
          />
        )}
        <div className="table-footer">
          <span>{filtered.length} documents</span>
          <span>
            <ShieldCheck size={13} /> Sources stay inside their workspace
          </span>
        </div>
      </section>
      <div className="info-strip">
        <BookOpen size={18} />
        <p>
          <strong>
            Knowledge evolves. Published answers stay reproducible.
          </strong>{" "}
          New uploads become a candidate knowledge revision. Save and evaluate a
          workflow version to use them in a published assistant.
        </p>
      </div>
      {view && (
        <Modal title={view.name} close={() => setView(null)} wide>
          <div className="source-details">
            <Badge value={view.status} />
            <span>Version {view.version}</span>
            <span>{view.chunk_count} chunks</span>
          </div>
          <ErrorBox message={chunks.error} />
          {chunks.loading ? (
            <Loading />
          ) : (
            <div className="chunk-list">
              {chunks.data?.map((c) => (
                <div key={c.id}>
                  <div className="chunk-label">
                    {c.location} <span>Chunk {c.ordinal + 1}</span>
                  </div>
                  <div className="document-text">
                    <ReactMarkdown>{c.text}</ReactMarkdown>
                  </div>
                </div>
              ))}
              {!chunks.data?.length && (
                <p className="muted">
                  Indexed content will appear here when ingestion completes.
                </p>
              )}
            </div>
          )}
        </Modal>
      )}
      {deleting && (
        <Modal title="Revoke this source?" close={() => setDeleting(null)}>
          <p>
            <strong>{deleting.name}</strong> will become inaccessible
            immediately. Any published assistant using this source will pause
            until a new version passes evaluation.
          </p>
          <div className="modal-actions">
            <button className="button" onClick={() => setDeleting(null)}>
              Keep document
            </button>
            <button
              className="button destructive"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await api(wsPath(workspace.id, `/documents/${deleting.id}`), {
                    method: "DELETE",
                  });
                  setDeleting(null);
                  docs.reload();
                }, "Source revoked.")
              }
            >
              Revoke source
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
