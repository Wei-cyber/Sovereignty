import EmailDraft from "./EmailDraft";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  FileText,
  History,
  MessageSquare,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { api, post, wsPath } from "../api";
import {
  Badge,
  Duration,
  ErrorBox,
  Modal,
  useAction,
  useApp,
  useResource,
} from "../ui";
import type { Conversation, Document, Run, Source, Workflow } from "../types";

export default function AssistantPage() {
  const { workspace, user, config, navigate, notify, refresh } = useApp();
  const workflows = useResource<Workflow[]>(
    wsPath(workspace.id, "/workflows"),
    4000,
  );
  const documents = useResource<Document[]>(
    wsPath(workspace.id, "/documents"),
    4000,
  );
  const conversations = useResource<Conversation[]>(
    wsPath(workspace.id, "/conversations"),
    5000,
  );
  const published = (workflows.data || []).filter(
    (w) => w.owner_id === user.id || (w.published_version_id && !w.paused),
  );
  const [workflowId, setWorkflowId] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversation = useResource<Conversation>(
    conversationId
      ? wsPath(workspace.id, `/conversations/${conversationId}`)
      : null,
    5000,
  );
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [question, setQuestion] = useState("");
  const [stage, setStage] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [feedback, setFeedback] = useState<{ run: Run; rating: number } | null>(
    null,
  );
  const [feedbackText, setFeedbackText] = useState("");
  const [rated, setRated] = useState<Record<string, number>>({});
  const { busy, act } = useAction();
  const bottom = useRef<HTMLDivElement>(null);
  const running = activeRun && ["queued", "running"].includes(activeRun.status);
  const chatRuns = [
    ...(conversation.data?.id === conversationId
      ? conversation.data.runs || []
      : []),
  ];
  if (activeRun && activeRun.conversation_id === conversationId) {
    const at = chatRuns.findIndex((r) => r.id === activeRun.id);
    if (at >= 0 && running) chatRuns[at] = activeRun;
    else if (at < 0) chatRuns.push(activeRun);
  }
  useEffect(() => {
    if (!workflowId && published.length) setWorkflowId(published[0].id);
  }, [published, workflowId]);
  useEffect(() => {
    workflows.reload();
    documents.reload();
  }, [refresh]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activeRun?.status, conversationId]);
  useEffect(() => {
    if (!activeRun || !["queued", "running"].includes(activeRun.status)) return;
    const runId = activeRun.id;
    const events = new EventSource(
      "/api/v1" + wsPath(workspace.id, `/runs/${runId}/events`),
      { withCredentials: true },
    );
    let stopped = false;
    const update = async () => {
      try {
        const run = await api<Run>(wsPath(workspace.id, `/runs/${runId}`));
        if (stopped) return;
        setActiveRun(run);
        if (!["queued", "running"].includes(run.status)) {
          events.close();
          conversation.reload();
          conversations.reload();
        }
      } catch (e) {
        if (!stopped) notify((e as Error).message, true);
      }
    };
    events.addEventListener("step_started", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setStage(data.label || "Working with your knowledge");
    });
    events.addEventListener("status", update);
    events.addEventListener("done", update);
    events.addEventListener("access_revoked", () => {
      events.close();
      setActiveRun(null);
      setConversationId(null);
      notify("Workspace access changed. Please refresh.", true);
    });
    const timer = setInterval(update, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
      events.close();
    };
  }, [activeRun?.id, running]);
  const send = () =>
    act(async () => {
      if (!question.trim() || !workflowId) return;
      const run = await api<Run>(
        wsPath(workspace.id, `/workflows/${workflowId}/runs`),
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({
            question: question.trim(),
            conversation_id: conversationId,
          }),
        },
      );
      setActiveRun(run);
      setConversationId(run.conversation_id);
      setQuestion("");
      setStage("Finding the right sources");
      conversations.reload();
    });
  const readyDocs = (documents.data || []).filter(
    (d) => d.status === "ready" && !d.superseded_revision,
  );
  return (
    <div className="assistant-page">
      <div className="assistant-toolbar">
        <div>
          <span className="small-icon">
            <Sparkles size={16} />
          </span>
          <strong>Knowledge assistant</strong>
          <span className="subtle">Grounded in your workspace</span>
        </div>
        <div>
          <button className="button small" onClick={() => setHistoryOpen(true)}>
            <History size={15} /> History
          </button>
          <button
            className="button small"
            onClick={() => {
              setConversationId(null);
              setActiveRun(null);
              setQuestion("");
            }}
            disabled={!!running}
          >
            <Plus size={15} /> New chat
          </button>
        </div>
      </div>
      <ErrorBox message={workflows.error || conversation.error} />
      {!chatRuns.length ? (
        <div className="assistant-welcome">
          <div className="welcome-art" aria-hidden="true">
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <span className="orbit-document">
              <FileText size={20} />
            </span>
            <span className="orbit-check">
              <Check size={16} />
            </span>
            <div className="welcome-spark">
              <Sparkles size={33} />
            </div>
          </div>
          <div className="eyebrow">A LITTLE CLARITY GOES A LONG WAY</div>
          <h1>
            What can we help you
            <br />
            make sense of, {user.name.split(" ")[0]}?
          </h1>
          <p>
            Find the answer in your team's knowledge.
            <br />
            Every response connected to its source.
          </p>
          <div className="suggestion-grid">
            {(
              published
                .find((w) => w.id === workflowId)
                ?.starters?.filter(Boolean)
                .map((text) => ({ icon: Sparkles, title: text, text })) || []
            ).length
              ? published
                  .find((w) => w.id === workflowId)!
                  .starters!.filter(Boolean)
                  .map((text) => (
                    <button
                      className="suggestion"
                      key={text}
                      onClick={() => setQuestion(text)}
                    >
                      {text}
                    </button>
                  ))
              : [
                  {
                    icon: BookOpen,
                    title: "Get up to speed",
                    text: "How long is the new employee onboarding program?",
                  },
                  {
                    icon: Search,
                    title: "Find the details",
                    text: "How many reviewers must approve a code change?",
                  },
                  {
                    icon: WorkflowIcon,
                    title: "Understand a process",
                    text: "How frequently must access permissions be reviewed?",
                  },
                ].map((s) => (
                  <button
                    key={s.title}
                    className="suggestion"
                    onClick={() => setQuestion(s.text)}
                  >
                    <s.icon size={20} />
                    <strong>{s.title}</strong>
                    <p>{s.text}</p>
                    <ArrowUp size={15} />
                  </button>
                ))}
          </div>
          {!published.length && (
            <div className="onboarding-banner">
              <div>
                <span className="small-icon">
                  <WorkflowIcon size={18} />
                </span>
                <strong>Create your first personal agent.</strong>
                <p>
                  {workspace.role === "admin"
                    ? "Review an evaluation suite, test a workflow, and publish when it passes."
                    : "Open Agents to create an assistant you can use privately right away."}
                </p>
              </div>
              {workspace.role === "admin" && (
                <button
                  className="button primary small"
                  onClick={() => navigate("workflows")}
                >
                  Open workflows
                  <ArrowRight size={15} />
                </button>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="chat-messages">
          {chatRuns.map((run) => (
            <div className="chat-turn" key={run.id}>
              <div className="user-message">
                <div className="avatar">{user.name[0]}</div>
                <div>
                  <span>You</span>
                  <p>{run.question}</p>
                </div>
              </div>
              <div className="assistant-message">
                <span className="answer-avatar">
                  <Sparkles size={18} />
                </span>
                <div className="answer-body">
                  <div className="answer-heading">
                    <strong>Relay</strong>
                    <Badge value={run.status} />
                  </div>
                  {run.content_revoked ? (
                    <ErrorBox message="This answer is unavailable because a source was revoked." />
                  ) : run.answer ? (
                    <>
                      <div className="markdown">
                        <ReactMarkdown>{run.answer.text}</ReactMarkdown>
                      </div>
                      {run.answer.draft && (
                        <EmailDraft draft={run.answer.draft} runId={run.id} />
                      )}
                      {run.answer.citations.length > 0 && (
                        <div className="answer-sources">
                          {run.answer.citations.map((citation, i) => {
                            const found = run.sources.find(
                              (s) => s.chunk_id === citation.chunk_id,
                            );
                            return (
                              found && (
                                <button
                                  className="source-chip"
                                  key={citation.chunk_id + i}
                                  onClick={() =>
                                    act(async () => {
                                      const current = await api<Run>(
                                        wsPath(workspace.id, `/runs/${run.id}`),
                                      );
                                      const evidence = current.sources.find(
                                        (s) => s.chunk_id === found.chunk_id,
                                      );
                                      if (!evidence || current.content_revoked)
                                        throw new Error(
                                          "This source is no longer accessible.",
                                        );
                                      setSource(evidence);
                                    })
                                  }
                                >
                                  <span>{i + 1}</span>
                                  <FileText size={14} />
                                  {found.document_name.replace(
                                    /\.(md|txt|pdf)$/i,
                                    "",
                                  )}
                                  <ArrowUp size={13} />
                                </button>
                              )
                            );
                          })}
                        </div>
                      )}
                      <div className="answer-meta">
                        <ShieldCheck size={13} />
                        <span>
                          {run.answer.abstained
                            ? "Insufficient evidence"
                            : run.answer.draft ? "Draft for review" : "Sources verified"}
                        </span>
                        <span>·</span>
                        <Duration ms={run.latency_ms} />
                        <span className="feedback-buttons">
                          <button
                            aria-label="Helpful answer"
                            className={rated[run.id] === 1 ? "selected" : ""}
                            onClick={() => {
                              setFeedback({ run, rating: 1 });
                              setFeedbackText("");
                            }}
                          >
                            <ThumbsUp size={14} />
                          </button>
                          <button
                            aria-label="Unhelpful answer"
                            className={rated[run.id] === -1 ? "selected" : ""}
                            onClick={() => {
                              setFeedback({ run, rating: -1 });
                              setFeedbackText("");
                            }}
                          >
                            <ThumbsDown size={14} />
                          </button>
                        </span>
                      </div>
                    </>
                  ) : ["queued", "running"].includes(run.status) ? (
                    <div className="thinking">
                      <span className="thinking-dots">
                        <i />
                        <i />
                        <i />
                      </span>
                      {stage || "Working with your knowledge…"}
                    </div>
                  ) : (
                    <ErrorBox
                      message={run.error || "This run was cancelled."}
                    />
                  )}
                </div>
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
      )}
      <div className="composer-area">
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            aria-label="Ask your workspace"
            placeholder={
              published.length
                ? "Ask anything about your workspace…"
                : "Create an agent or select a published assistant to start."
            }
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!running && !busy && published.length) send();
              }
            }}
            rows={2}
          />
          <div className="composer-footer">
            <div className="assistant-select">
              <Sparkles size={14} />
              <select
                aria-label="Select assistant"
                value={workflowId}
                disabled={!!conversationId || !!running}
                onChange={(e) => setWorkflowId(e.target.value)}
              >
                {!published.length && (
                  <option value="">No assistant available</option>
                )}
                {published.map((w) => (
                  <option value={w.id} key={w.id}>
                    {w.name}
                    {w.owner_id ? " · Not reviewed" : ""}
                  </option>
                ))}
              </select>
              <ChevronDown size={13} />
            </div>
            {running ? (
              <button
                className="send-button stop"
                aria-label="Stop response"
                type="button"
                onClick={() =>
                  act(async () => {
                    await post(
                      wsPath(workspace.id, `/runs/${activeRun.id}/cancel`),
                    );
                  })
                }
              >
                <Square size={16} />
              </button>
            ) : (
              <button
                className="send-button"
                aria-label="Send question"
                disabled={
                  busy ||
                  !question.trim() ||
                  !published.some((w) => w.id === workflowId)
                }
              >
                <ArrowUp size={20} />
              </button>
            )}
          </div>
        </form>
        <div className="composer-note">
          <ShieldCheck size={12} />
          Answers use your permitted workspace knowledge. Always review
          important details.
        </div>
      </div>
      {!chatRuns.length && (
        <div className="workspace-snapshot">
          <div className="snapshot-title">CONNECTED TO YOUR WORKSPACE</div>
          <div>
            <span>
              <BookOpen size={16} />
              <strong>{readyDocs.length}</strong> indexed documents
            </span>
            <span>
              <WorkflowIcon size={16} />
              <strong>{published.length}</strong> published assistants
            </span>
            <span>
              <ShieldCheck size={16} /> Workspace access enforced
            </span>
          </div>
        </div>
      )}
      {historyOpen && (
        <Modal title="Your conversations" close={() => setHistoryOpen(false)}>
          <div className="history-list">
            {conversations.data?.length ? (
              conversations.data.map((c) => (
                <button
                  key={c.id}
                  onClick={() => {
                    setConversationId(c.id);
                    setWorkflowId(c.workflow_id);
                    setActiveRun(null);
                    setHistoryOpen(false);
                  }}
                >
                  <MessageSquare size={17} />
                  <span>{c.title}</span>
                  <ArrowRight size={16} />
                </button>
              ))
            ) : (
              <p className="muted">
                Your conversations will appear here after your first question.
              </p>
            )}
          </div>
        </Modal>
      )}
      {source && (
        <Modal title={source.document_name} close={() => setSource(null)} wide>
          <div className="source-details">
            <Badge value="ready">Version {source.document_version}</Badge>
            <span>
              {source.kind || "knowledge"} · {source.location}
            </span>
            {source.fetched_at && (
              <span>
                Retrieved {new Date(source.fetched_at).toLocaleString()}
              </span>
            )}
          </div>
          <div className="document-text">
            <ReactMarkdown>{source.text}</ReactMarkdown>
          </div>
        </Modal>
      )}
      {feedback && (
        <Modal title="Help improve this answer" close={() => setFeedback(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await post(
                  wsPath(workspace.id, `/runs/${feedback.run.id}/feedback`),
                  { rating: feedback.rating, comment: feedbackText },
                );
                setRated((v) => ({ ...v, [feedback.run.id]: feedback.rating }));
                setFeedback(null);
              }, "Feedback saved for administrator review.");
            }}
          >
            <p className="muted">
              Your feedback helps the team identify better regression tests. It
              does not change the assistant automatically.
            </p>
            <label>
              What worked, or what should improve?
              <textarea
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                rows={4}
                maxLength={4000}
              />
            </label>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                Save feedback
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
