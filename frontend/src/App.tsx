import { useEffect, useState } from "react";
import "./agents.css";
import {
  ArrowRight,
  BookOpen,
  ChevronDown,
  CircleHelp,
  FileText,
  FlaskConical,
  Layers3,
  LayoutGrid,
  LogOut,
  Menu,
  MessageSquare,
  Play,
  Settings2,
  ShieldCheck,
  Sparkles,
  Workflow as WorkflowIcon,
  X,
} from "lucide-react";
import { api, post, setCsrf } from "./api";
import { AppContext, ErrorBox, Loading } from "./ui";
import type { Configuration, User, Workspace } from "./types";
import AssistantPage from "./pages/Assistant";
import KnowledgePage from "./pages/Knowledge";
import WorkflowsPage from "./pages/Workflows";
import RunsPage from "./pages/Runs";
import EvaluationsPage from "./pages/Evaluations";
import SettingsPage from "./pages/Settings";

import AgentsPage from "./pages/Agents";
import ConnectionsPage from "./pages/Connections";

const navigation = [
  { id: "agents", label: "Agents", icon: Sparkles },
  { id: "connections", label: "Connections", icon: Layers3 },
  { id: "assistant", label: "Assistant", icon: MessageSquare },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
  { id: "workflows", label: "Workflows", icon: WorkflowIcon, admin: true },
  { id: "runs", label: "Runs", icon: Play, admin: true },
  { id: "evaluations", label: "Evaluations", icon: FlaskConical, admin: true },
  { id: "settings", label: "Settings", icon: Settings2, admin: true },
];

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="login-screen">
      <div className="login-story">
        <div className="brand">
          <span className="brand-mark">
            <Layers3 size={25} />
          </span>
          relay<span className="brand-period">.</span>
        </div>
        <div className="login-copy">
          <span className="eyebrow">YOUR KNOWLEDGE. IN MOTION.</span>
          <h1>
            Good answers.
            <br />
            Great workflows.
          </h1>
          <p>
            Bring your team's knowledge and AI together.
            <br />
            Build with confidence. Improve with evidence.
          </p>
          <div className="login-flow">
            <div>
              <FileText />
              <span>Knowledge</span>
            </div>
            <span className="flow-line" />
            <div>
              <WorkflowIcon />
              <span>Workflow</span>
            </div>
            <span className="flow-line" />
            <div>
              <Sparkles />
              <span>Answer</span>
            </div>
          </div>
        </div>
        <div className="login-footer">
          <ShieldCheck size={17} /> A private space for your enterprise
        </div>
      </div>
      <div className="login-form-area">
        <form
          className="login-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              const result = await post<{ user: User; csrf_token: string }>(
                "/auth/login",
                { email, password },
              );
              setCsrf(result.csrf_token);
              onLogin(result.user);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <span className="eyebrow">WELCOME TO RELAY</span>
          <h2>
            A little less searching.
            <br />A lot more doing.
          </h2>
          <p>Sign in to your team's AI workspace.</p>
          <label>
            Email address
            <input
              type="email"
              autoComplete="username"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <ErrorBox message={error} />
          <button className="button primary full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in to workspace"}
            <ArrowRight size={18} />
          </button>
          <div className="login-note">
            Accounts are managed by your workspace administrator.
          </div>
        </form>
      </div>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [config, setConfig] = useState<Configuration | null>(null);
  const [page, setPage] = useState(location.hash.slice(1) || "assistant");
  const [refresh, setRefresh] = useState(0);
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [loadError, setLoadError] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  useEffect(() => {
    api<{ user: User; csrf_token: string }>("/auth/me")
      .then((result) => {
        setUser(result.user);
        setCsrf(result.csrf_token);
      })
      .catch(() => {})
      .finally(() => setInitializing(false));
    const expire = () => setUser(null);
    window.addEventListener("session-expired", expire);
    return () => window.removeEventListener("session-expired", expire);
  }, []);
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    Promise.all([
      api<Workspace[]>("/workspaces"),
      api<Configuration>("/configuration"),
    ])
      .then(([spaces, configuration]) => {
        if (cancelled) return;
        setWorkspaces(spaces);
        setConfig(configuration);
        setLoadError("");
        setWorkspaceId((current) => {
          let saved = "";
          try {
            saved = sessionStorage.getItem(`relay-workspace-${user.id}`) || "";
          } catch {
            /* Storage can be disabled by the browser. */
          }
          return (
            [current, saved].find((id) => spaces.some((s) => s.id === id)) ||
            spaces[0]?.id ||
            ""
          );
        });
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [user, refresh]);
  useEffect(() => {
    const change = () => setPage(location.hash.slice(1) || "assistant");
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), toast.error ? 9000 : 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  const navigate = (value: string) => {
    setPage(value);
    location.hash = value;
    setMobileNav(false);
  };
  if (initializing)
    return (
      <div className="app-loading">
        <Layers3 size={38} />
        <Loading />
      </div>
    );
  if (!user) return <Login onLogin={setUser} />;
  const workspace = workspaces.find((w) => w.id === workspaceId);
  if (!workspace || !config)
    return (
      <div className="app-loading">
        <Layers3 size={38} />
        {loadError ? (
          <>
            <ErrorBox message={loadError} />
            <button className="button" onClick={() => setRefresh((n) => n + 1)}>
              Retry
            </button>
          </>
        ) : workspaces.length === 0 && config ? (
          <>
            <h2>No workspace access yet</h2>
            <p>Ask your administrator to add you to a workspace.</p>
            <button
              className="button"
              onClick={() => post("/auth/logout").then(() => setUser(null))}
            >
              Sign out
            </button>
          </>
        ) : (
          <Loading />
        )}
      </div>
    );
  const admin = workspace.role === "admin";
  const currentPage =
    navigation.find((n) => n.id === page && (!n.admin || admin))?.id ||
    "assistant";
  const pages: Record<string, React.ReactNode> = {
    assistant: <AssistantPage />,
    agents: <AgentsPage />,
    connections: <ConnectionsPage />,
    knowledge: <KnowledgePage />,
    workflows: <WorkflowsPage />,
    runs: <RunsPage />,
    evaluations: <EvaluationsPage />,
    settings: <SettingsPage />,
  };
  return (
    <AppContext.Provider
      value={{
        user,
        workspace,
        config,
        refresh,
        notify: (text, error = false) => setToast({ text, error }),
        invalidate: () => setRefresh((n) => n + 1),
        navigate,
      }}
    >
      <div className="app-shell">
        <aside
          id="workspace-navigation"
          className={`sidebar ${mobileNav ? "open" : ""}`}
        >
          <a className="brand" href="#assistant">
            <span className="brand-mark">
              <Layers3 size={24} />
            </span>
            relay<span className="brand-period">.</span>
          </a>
          <div className="workspace-picker">
            <div className="workspace-avatar">{workspace.name[0]}</div>
            <div>
              <span className="workspace-name">{workspace.name}</span>
              <span className="workspace-type">Team workspace</span>
            </div>
            <ChevronDown size={15} />
            <select
              aria-label="Switch workspace"
              value={workspaceId}
              onChange={(e) => {
                setWorkspaceId(e.target.value);
                try {
                  sessionStorage.setItem(
                    `relay-workspace-${user.id}`,
                    e.target.value,
                  );
                } catch {
                  /* Workspace selection still works without storage. */
                }
                navigate("assistant");
              }}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </div>
          <div className="nav-label">WORKSPACE</div>
          <nav>
            {navigation
              .filter((n) => !n.admin || admin)
              .map((n) => (
                <button
                  key={n.id}
                  className={`nav-item ${n.id === currentPage ? "active" : ""}`}
                  onClick={() => navigate(n.id)}
                >
                  <n.icon size={19} />
                  <span>{n.label}</span>
                  {n.id === currentPage && <span className="active-dot" />}
                </button>
              ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="private-card">
              <ShieldCheck size={19} />
              <div>
                <strong>Your workspace, protected</strong>
                <p>Private knowledge. Controlled access.</p>
              </div>
            </div>
            <button
              className="nav-item help-link"
              onClick={() => {
                setToast({
                  text: "Upload documents in Knowledge. In Evaluations, create a suite using the question form and complete Grader check. Then save, evaluate, and publish your workflow.",
                  error: false,
                });
              }}
            >
              <CircleHelp size={18} />
              <span>Getting started</span>
              <ArrowRight size={15} />
            </button>
            <div className="profile">
              <div className="avatar">
                {user.name
                  .split(" ")
                  .slice(0, 2)
                  .map((n) => n[0])
                  .join("")}
              </div>
              <div>
                <strong>{user.name}</strong>
                <span>{admin ? "Workspace admin" : "Workspace member"}</span>
              </div>
              <button
                title="Sign out"
                aria-label="Sign out"
                onClick={() =>
                  post("/auth/logout")
                    .then(() => setUser(null))
                    .catch((e) => setToast({ text: e.message, error: true }))
                }
              >
                <LogOut size={17} />
              </button>
            </div>
          </div>
        </aside>
        {mobileNav && (
          <button
            className="nav-overlay"
            aria-label="Close navigation"
            onClick={() => setMobileNav(false)}
          />
        )}
        <main className="main">
          <header className="topbar">
            <div className="breadcrumbs">
              <button
                className="mobile-menu icon-button"
                aria-label="Open navigation"
                aria-expanded={mobileNav}
                aria-controls="workspace-navigation"
                onClick={() => setMobileNav(true)}
              >
                <Menu size={20} />
              </button>
              <LayoutGrid size={16} />
              <span>{workspace.name}</span>
              <span className="breadcrumb-slash">/</span>
              <strong>
                {navigation.find((n) => n.id === currentPage)?.label}
              </strong>
            </div>
            <div className="topbar-right">
              <span className="environment-pill">
                <span />
                Private workspace
              </span>
              <span className="top-avatar">{user.name[0]}</span>
            </div>
          </header>
          <div className="page-content" key={`${workspace.id}-${currentPage}`}>
            {pages[currentPage]}
          </div>
        </main>
        {toast && (
          <div className={`toast ${toast.error ? "error" : ""}`} role="status">
            <span>{toast.text}</span>
            <button
              aria-label="Dismiss notification"
              onClick={() => setToast(null)}
            >
              <X size={17} />
            </button>
          </div>
        )}
      </div>
    </AppContext.Provider>
  );
}
