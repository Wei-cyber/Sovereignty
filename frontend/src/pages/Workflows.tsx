import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import type { Connection, Edge, Node, NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Copy,
  FileOutput,
  FlaskConical,
  GitBranch,
  Grip,
  LayoutGrid,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { post, wsPath } from "../api";
import {
  Badge,
  Duration,
  Empty,
  ErrorBox,
  Modal,
  PageHeader,
  useAction,
  useApp,
  useResource,
} from "../ui";
import { graphProblem } from "../workflow";
import type {
  Definition,
  Run,
  Version,
  Workflow,
  WorkflowNode,
} from "../types";

const icons = {
  input: MessageSquare,
  retrieval: Search,
  model: Sparkles,
  condition: GitBranch,
  agent: Bot,
  answer: FileOutput,
};
const descriptions = {
  input: "A question starts the workflow",
  retrieval: "Find relevant workspace sources",
  model: "Compose an answer from evidence",
  condition: "Choose the next path",
  agent: "Search, inspect, and reason",
  answer: "Return a cited answer",
};
type FlowNode = Node<{ step: WorkflowNode }, "step">;

function StepNode({ data, selected }: NodeProps<FlowNode>) {
  const Icon = icons[data.step.type];
  return (
    <div className={`workflow-node ${selected ? "selected" : ""}`}>
      {data.step.type !== "input" && (
        <Handle type="target" position={Position.Left} />
      )}
      <div className="node-top">
        <span className={`node-icon ${data.step.type}`}>
          <Icon size={17} />
        </span>
        <span>
          {data.step.type === "model" ? "Language model" : data.step.type}
        </span>
        <Grip size={13} />
      </div>
      <strong>{data.step.label}</strong>
      <p>{descriptions[data.step.type]}</p>
      {data.step.type === "condition" ? (
        <>
          <Handle
            type="source"
            id="true"
            position={Position.Right}
            style={{ top: "38%" }}
          />
          <span className="handle-label true">True</span>
          <Handle
            type="source"
            id="false"
            position={Position.Right}
            style={{ top: "76%" }}
          />
          <span className="handle-label false">False</span>
        </>
      ) : (
        data.step.type !== "answer" && (
          <Handle type="source" position={Position.Right} />
        )
      )}
    </div>
  );
}
const nodeTypes = { step: StepNode };

function Editor({
  workflow,
  back,
  reload,
}: {
  workflow: Workflow;
  back: () => void;
  reload: () => void;
}) {
  const { workspace, navigate, notify } = useApp();
  const [versionId, setVersionId] = useState(workflow.versions[0].id);
  const version =
    workflow.versions.find((v) => v.id === versionId) || workflow.versions[0];
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [question, setQuestion] = useState(
    "How many vacation days do employees receive?",
  );
  const [runId, setRunId] = useState<string | null>(null);
  const run = useResource<Run>(
    runId ? wsPath(workspace.id, `/runs/${runId}`) : null,
    1500,
  );
  const { busy, act } = useAction();
  useEffect(() => {
    setNodes(
      version.definition.nodes.map((step) => ({
        id: step.id,
        type: "step",
        position: { x: step.position?.x || 0, y: step.position?.y || 0 },
        data: { step },
      })),
    );
    setEdges(
      version.definition.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.branch || undefined,
        label: e.branch || "",
        type: "smoothstep",
      })),
    );
    setSelectedId(null);
  }, [version.id]);
  const definition = useMemo<Definition>(
    () => ({
      schema_version: 1,
      nodes: nodes.map((n) => ({ ...n.data.step, position: n.position })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        branch: (e.sourceHandle || null) as "true" | "false" | null,
      })),
    }),
    [nodes, edges],
  );
  const dirty =
    JSON.stringify(definition) !== JSON.stringify(version.definition);
  const selected = nodes.find((n) => n.id === selectedId);
  const connect = useCallback(
    (connection: Connection) =>
      setEdges((es) =>
        addEdge(
          {
            ...connection,
            type: "smoothstep",
            label: connection.sourceHandle || "",
          },
          es,
        ),
      ),
    [],
  );
  const updateStep = (change: Partial<WorkflowNode>) =>
    setNodes((items) =>
      items.map((n) =>
        n.id === selectedId
          ? { ...n, data: { step: { ...n.data.step, ...change } } }
          : n,
      ),
    );
  const add = (type: WorkflowNode["type"]) => {
    const id = `${type}_${crypto.randomUUID().slice(0, 8)}`;
    const step: WorkflowNode = {
      id,
      type,
      label:
        type === "agent"
          ? "Research agent"
          : `${type[0].toUpperCase()}${type.slice(1)} step`,
      position: { x: nodes.length * 220, y: 240 },
      config: {
        prompt:
          "Answer the question using only the supplied evidence. Cite your sources.",
        top_k: 5,
        max_tool_calls: 4,
        condition: "has_sources",
      },
    };
    setNodes((items) => [
      ...items,
      {
        id,
        type: "step",
        position: { x: step.position!.x, y: step.position!.y },
        data: { step },
      },
    ]);
    setSelectedId(id);
  };
  return (
    <div className="workflow-editor">
      <div className="editor-heading">
        <button
          className="icon-button"
          aria-label="Back to workflows"
          onClick={back}
        >
          <ArrowLeft size={20} />
        </button>
        <div>
          <h1>{workflow.name}</h1>
          <p>{workflow.description}</p>
        </div>
        <Badge
          value={
            workflow.paused
              ? "paused"
              : workflow.published_version_id === version.id
                ? "published"
                : "draft"
          }
        />
      </div>
      <div className="editor-toolbar">
        <div>
          <select
            aria-label="Workflow version"
            value={version.id}
            onChange={(e) => setVersionId(e.target.value)}
          >
            {workflow.versions.map((v) => (
              <option key={v.id} value={v.id}>
                Version {v.number}
                {v.id === workflow.published_version_id ? " · Published" : ""}
              </option>
            ))}
          </select>
          <span className="subtle">
            Knowledge revision {version.corpus_revision}
          </span>
          {dirty && <span className="unsaved">Unsaved changes</span>}
        </div>
        <div>
          <button
            className="button small"
            disabled={busy || dirty}
            onClick={() => setPreview(true)}
          >
            <Play size={15} /> Preview
          </button>
          <button
            className="button small"
            disabled={busy}
            onClick={() => {
              sessionStorage.setItem("evaluateWorkflow", workflow.id);
              sessionStorage.setItem("evaluateVersion", version.id);
              navigate("evaluations");
            }}
          >
            <FlaskConical size={15} /> Evaluate
          </button>
          <button
            className="button primary small"
            disabled={busy || !!graphProblem(definition)}
            onClick={() =>
              act(async () => {
                const result = await post<Version>(
                  wsPath(workspace.id, `/workflows/${workflow.id}/versions`),
                  definition,
                );
                reload();
                setVersionId(result.id);
              }, "Saved an immutable workflow version.")
            }
          >
            <Save size={15} /> Save new version
          </button>
        </div>
      </div>
      <div className="editor-workspace">
        <aside className="node-library">
          <div className="eyebrow">BUILDING BLOCKS</div>
          <p>Add a step, then connect it.</p>
          {(Object.keys(icons) as WorkflowNode["type"][]).map((type) => {
            const Icon = icons[type];
            return (
              <button
                className="library-node"
                key={type}
                onClick={() => add(type)}
                disabled={
                  type === "input" &&
                  nodes.some((n) => n.data.step.type === "input")
                }
              >
                <span className={`node-icon ${type}`}>
                  <Icon size={17} />
                </span>
                <span>
                  {type === "model"
                    ? "Model"
                    : type[0].toUpperCase() + type.slice(1)}
                </span>
                <Plus size={14} />
              </button>
            );
          })}
          <div className="library-note">
            <ShieldCheck size={16} />
            <span>Agent tools can only search and read your workspace.</span>
          </div>
        </aside>
        <div className="flow-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            onNodeClick={(_, node) => setSelectedId(node.id)}
            onPaneClick={() => setSelectedId(null)}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.2}
            maxZoom={1.5}
            deleteKeyCode={["Backspace", "Delete"]}
          >
            <Background color="#d8e0da" gap={20} />
            <Controls showInteractive={false} />
            <MiniMap nodeColor="#c9ddd1" maskColor="rgba(246,248,244,.7)" />
          </ReactFlow>
          <div className="canvas-caption">
            <span className="live-dot" /> {nodes.length} steps · {edges.length}{" "}
            connections
          </div>
        </div>
        <aside className="node-inspector">
          <div className="eyebrow">
            <Settings2 size={14} /> STEP SETTINGS
          </div>
          {selected ? (
            <>
              <span className="inspector-kind">{selected.data.step.type}</span>
              <label>
                Step name
                <input
                  value={selected.data.step.label}
                  onChange={(e) => updateStep({ label: e.target.value })}
                  maxLength={80}
                />
              </label>
              {["model", "agent"].includes(selected.data.step.type) && (
                <label>
                  Task instructions
                  <textarea
                    rows={9}
                    value={selected.data.step.config?.prompt || ""}
                    onChange={(e) =>
                      updateStep({
                        config: {
                          ...selected.data.step.config!,
                          prompt: e.target.value,
                        },
                      })
                    }
                    maxLength={8000}
                  />
                </label>
              )}
              {["retrieval", "agent"].includes(selected.data.step.type) && (
                <label>
                  Sources to retrieve
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={selected.data.step.config?.top_k || 5}
                    onChange={(e) =>
                      updateStep({
                        config: {
                          ...selected.data.step.config!,
                          top_k: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
              )}
              {selected.data.step.type === "agent" && (
                <label>
                  Maximum tool calls
                  <input
                    type="number"
                    min={1}
                    max={4}
                    value={selected.data.step.config?.max_tool_calls || 4}
                    onChange={(e) =>
                      updateStep({
                        config: {
                          ...selected.data.step.config!,
                          max_tool_calls: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
              )}
              {selected.data.step.type === "condition" && (
                <label>
                  Condition
                  <select
                    value={
                      selected.data.step.config?.condition || "has_sources"
                    }
                    onChange={(e) =>
                      updateStep({
                        config: {
                          ...selected.data.step.config!,
                          condition: e.target.value as
                            "has_sources" | "has_answer" | "abstained",
                        },
                      })
                    }
                  >
                    <option value="has_sources">Sources were found</option>
                    <option value="has_answer">An answer is available</option>
                    <option value="abstained">The model abstained</option>
                  </select>
                </label>
              )}
              <p className="field-help">
                {descriptions[selected.data.step.type]}
              </p>
              <button
                className="button danger small"
                onClick={() => {
                  setNodes((ns) => ns.filter((n) => n.id !== selectedId));
                  setEdges((es) =>
                    es.filter(
                      (e) => e.source !== selectedId && e.target !== selectedId,
                    ),
                  );
                  setSelectedId(null);
                }}
              >
                <Trash2 size={14} /> Remove step
              </button>
            </>
          ) : (
            <div className="inspector-empty">
              <WorkflowIcon size={25} />
              <h3>Make it your workflow</h3>
              <p>
                Select a step to edit its instructions and settings. Drag
                between handles to connect steps.
              </p>
            </div>
          )}
        </aside>
      </div>
      <div className="editor-bottom">
        {graphProblem(definition) ? (
          <span className="validation-warning">{graphProblem(definition)}</span>
        ) : (
          <span>
            <ShieldCheck size={14} /> Graph connections are valid
          </span>
        )}
        <span>Published versions require a passing evaluation.</span>
      </div>
      {preview && (
        <Modal title="Preview workflow" close={() => setPreview(false)} wide>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                const result = await post<Run>(
                  wsPath(workspace.id, `/workflows/${workflow.id}/runs`),
                  { question, version_id: version.id, preview: true },
                );
                setRunId(result.id);
              });
            }}
          >
            <p className="muted">
              Preview uses version {version.number} and the same execution
              engine as the published assistant.
            </p>
            <label>
              Test question
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={3}
                required
              />
            </label>
            <div className="modal-actions">
              <button
                className="button primary"
                disabled={
                  busy ||
                  (!!run.data &&
                    ["queued", "running"].includes(run.data.status))
                }
              >
                <Play size={15} /> Run preview
              </button>
            </div>
          </form>
          {run.data && (
            <div className="preview-result">
              <div className="section-title">
                <Badge value={run.data.status} />
                <Duration ms={run.data.latency_ms} />
              </div>
              <ErrorBox message={run.data.error || run.error} />
              {run.data.answer && (
                <>
                  <p>{run.data.answer.text}</p>
                  <div className="source-count">
                    <FileOutput size={15} />
                    {run.data.answer.citations.length} citations ·{" "}
                    {run.data.tokens} tokens
                  </div>
                </>
              )}
              <button
                className="text-button"
                onClick={() => {
                  sessionStorage.setItem("inspectRun", run.data!.id);
                  navigate("runs");
                }}
              >
                Inspect run
                <ArrowRight size={14} />
              </button>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

export default function WorkflowsPage() {
  const { workspace, refresh } = useApp();
  const workflows = useResource<Workflow[]>(
    wsPath(workspace.id, "/workflows"),
    4000,
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("rag");
  const [description, setDescription] = useState("");
  const { busy, act } = useAction();
  useEffect(() => workflows.reload(), [refresh]);
  const workflow = workflows.data?.find((w) => w.id === selected);
  if (workflow)
    return (
      <Editor
        workflow={workflow}
        back={() => setSelected(null)}
        reload={workflows.reload}
      />
    );
  return (
    <>
      <PageHeader
        eyebrow="BUILD WITH INTENTION"
        title="From knowledge to action"
        description="Connect the steps. Test the answers. Publish with confidence."
      >
        <button
          className="button primary"
          onClick={() => {
            setName("");
            setDescription("");
            setCreating(true);
          }}
        >
          <Plus size={17} /> Create workflow
        </button>
      </PageHeader>
      <ErrorBox message={workflows.error} />
      <div className="section-heading">
        <div>
          <h2>
            Your workflows{" "}
            <span className="count">{workflows.data?.length || 0}</span>
          </h2>
          <p>Reusable processes, with room for a little intelligence.</p>
        </div>
        <span className="view-toggle">
          <LayoutGrid size={17} />
        </span>
      </div>
      <div className="workflow-grid">
        {workflows.data?.map((workflow) => (
          <article className="workflow-card" key={workflow.id}>
            <div className="workflow-card-top">
              <span
                className={`workflow-card-icon ${workflow.versions[0].definition.nodes.some((n) => n.type === "agent") ? "agent" : ""}`}
              >
                {workflow.versions[0].definition.nodes.some(
                  (n) => n.type === "agent",
                ) ? (
                  <Bot size={23} />
                ) : (
                  <WorkflowIcon size={23} />
                )}
              </span>
              <Badge
                value={
                  workflow.paused
                    ? "paused"
                    : workflow.published_version_id
                      ? "published"
                      : "draft"
                }
              />
            </div>
            <button
              className="workflow-card-title"
              onClick={() => setSelected(workflow.id)}
            >
              <h3>{workflow.name}</h3>
              <ArrowUpRightIcon />
            </button>
            <p>
              {workflow.description ||
                "A reusable workflow for your workspace."}
            </p>
            <div className="mini-workflow">
              {workflow.versions[0].definition.nodes.slice(0, 5).map((n, i) => {
                const Icon = icons[n.type];
                return (
                  <div key={n.id}>
                    {i > 0 && <span className="mini-connector" />}
                    <span className={`mini-step ${n.type}`} title={n.label}>
                      <Icon size={17} />
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="workflow-card-footer">
              <span>
                {workflow.versions[0].definition.nodes.length} steps <b>·</b>{" "}
                Version {workflow.versions[0].number}
              </span>
              <button
                className="icon-button"
                title="Duplicate workflow"
                aria-label={`Duplicate ${workflow.name}`}
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const result = await post<Workflow>(
                      wsPath(
                        workspace.id,
                        `/workflows/${workflow.id}/duplicate`,
                      ),
                    );
                    workflows.reload();
                    setSelected(result.id);
                  }, "Workflow duplicated.")
                }
              >
                <Copy size={16} />
              </button>
              <button
                className="text-button"
                onClick={() => setSelected(workflow.id)}
              >
                Open
                <ArrowRight size={16} />
              </button>
            </div>
          </article>
        ))}
        <button
          className="new-workflow-card"
          onClick={() => {
            setName("");
            setCreating(true);
          }}
        >
          <span>
            <Plus size={24} />
          </span>
          <strong>Start something useful</strong>
          <p>Build a workflow from a proven template.</p>
        </button>
      </div>
      <div className="workflow-principles">
        <div>
          <span>01</span>
          <h3>Start with context</h3>
          <p>Connect the knowledge your team already trusts.</p>
        </div>
        <div>
          <span>02</span>
          <h3>Make the process yours</h3>
          <p>Combine structured steps with a bounded research agent.</p>
        </div>
        <div>
          <span>03</span>
          <h3>Let evidence lead</h3>
          <p>Evaluate every version before it reaches your team.</p>
        </div>
      </div>
      {creating && (
        <Modal title="Create a workflow" close={() => setCreating(false)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                const result = await post<Workflow>(
                  wsPath(workspace.id, "/workflows"),
                  { name, description, template: kind },
                );
                workflows.reload();
                setSelected(result.id);
                setCreating(false);
              }, "Workflow created.");
            }}
          >
            <label>
              Workflow name
              <input
                placeholder="e.g. People team assistant"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                maxLength={120}
              />
            </label>
            <label>
              Description
              <textarea
                rows={2}
                placeholder="What will this workflow help your team do?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={2000}
              />
            </label>
            <label>Starting template</label>
            <div className="template-options">
              <button
                type="button"
                className={kind === "rag" ? "selected" : ""}
                onClick={() => setKind("rag")}
              >
                <WorkflowIcon size={23} />
                <strong>Knowledge assistant</strong>
                <p>
                  Retrieve sources, compose an answer, and cite the evidence.
                </p>
              </button>
              <button
                type="button"
                className={kind === "agent" ? "selected" : ""}
                onClick={() => setKind("agent")}
              >
                <Bot size={23} />
                <strong>Research agent</strong>
                <p>
                  Let an agent search and inspect sources within clear limits.
                </p>
              </button>
            </div>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                <Plus size={16} /> Create workflow
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

function ArrowUpRightIcon() {
  return <ArrowRight size={18} className="diagonal-arrow" />;
}
