import { useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  KeyRound,
  Layers3,
  LockKeyhole,
  Plus,
  ShieldCheck,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { api, post, wsPath } from "../api";
import {
  Badge,
  DateLabel,
  ErrorBox,
  Modal,
  PageHeader,
  useAction,
  useApp,
  useResource,
} from "../ui";
import type { Audit, User } from "../types";

export default function SettingsPage() {
  const { workspace, user, config, refresh } = useApp();
  const members = useResource<(User & { role: string })[]>(
    wsPath(workspace.id, "/members"),
  );
  const accounts = useResource<User[]>(
    user.is_system_admin ? "/accounts" : null,
  );
  const events = useResource<Audit[]>(wsPath(workspace.id, "/audit"), 5000);
  const { busy, act } = useAction();
  const [dialog, setDialog] = useState<
    "account" | "member" | "workspace" | null
  >(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [memberId, setMemberId] = useState("");
  const [role, setRole] = useState("member");
  const [removing, setRemoving] = useState<(User & { role: string }) | null>(
    null,
  );
  useEffect(() => {
    members.reload();
    accounts.reload();
  }, [refresh]);
  return (
    <>
      <PageHeader
        eyebrow="A WORKSPACE THAT WORKS FOR YOU"
        title="People, access, and boundaries"
        description="Keep your team connected and your knowledge in the right hands."
      >
        {user.is_system_admin && (
          <button
            className="button"
            onClick={() => {
              setName("");
              setDialog("workspace");
            }}
          >
            <Plus size={16} /> New workspace
          </button>
        )}
      </PageHeader>
      <ErrorBox message={members.error || accounts.error} />
      <div className="settings-grid">
        <section className="panel settings-card">
          <div className="settings-card-heading">
            <span className="stat-icon mint">
              <Layers3 size={20} />
            </span>
            <div>
              <h2>{workspace.name}</h2>
              <p>Workspace details</p>
            </div>
          </div>
          <div className="setting-row">
            <span>Access model</span>
            <strong>Workspace membership</strong>
          </div>
          <div className="setting-row">
            <span>Sign-in</span>
            <strong>Local accounts</strong>
          </div>
          <div className="setting-row">
            <span>Knowledge revision</span>
            <strong>Revision {workspace.revision}</strong>
          </div>
          <div className="setting-row">
            <span>Release policy</span>
            <Badge value="reviewed">Evaluation required</Badge>
          </div>
        </section>
        <section className="panel settings-card">
          <div className="settings-card-heading">
            <span className="stat-icon cream">
              <ShieldCheck size={20} />
            </span>
            <div>
              <h2>AI configuration</h2>
              <p>
                Models configured by your operator
              </p>
            </div>
          </div>
          <div className="setting-row">
            <span>Answer model</span>
            <strong className="mono">
              {String(config.model_profile.chat_model)}
            </strong>
          </div>
          <div className="setting-row">
            <span>Evaluation model</span>
            <strong className="mono">
              {String(config.model_profile.grader_model)}
            </strong>
          </div>
          <div className="setting-row">
            <span>Agent permissions</span>
            <strong>Search and read only</strong>
          </div>
          <div className="setting-row">
            <span>Maximum run duration</span>
            <strong>{config.run_deadline_seconds} seconds</strong>
          </div>
          <div className="settings-footnote">
            <LockKeyhole size={14} />
            Necessary question context is sent to the configured model provider. Credentials stay on the server.
          </div>
        </section>
      </div>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>
              Workspace members{" "}
              <span className="count">{members.data?.length || 0}</span>
            </h2>
            <p>
              Members can use published assistants. Administrators also manage
              knowledge and workflows.
            </p>
          </div>
          <div className="header-actions">
            {user.is_system_admin && (
              <button
                className="button small"
                onClick={() => {
                  setName("");
                  setEmail("");
                  setPassword("");
                  setDialog("account");
                }}
              >
                <UserPlus size={15} /> Create account
              </button>
            )}
            <button
              className="button primary small"
              onClick={() => {
                setMemberId("");
                setRole("member");
                setDialog("member");
              }}
            >
              <Plus size={15} /> Add member
            </button>
          </div>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.data?.map((member) => (
                <tr key={member.id}>
                  <td>
                    <div className="person-cell">
                      <div className="avatar">
                        {member.name
                          .split(" ")
                          .slice(0, 2)
                          .map((n) => n[0])
                          .join("")}
                      </div>
                      <div>
                        <strong>
                          {member.name}
                          {member.id === user.id ? " (you)" : ""}
                        </strong>
                        <small>{member.email}</small>
                      </div>
                    </div>
                  </td>
                  <td>
                    <select
                      className="role-select"
                      aria-label={`Role for ${member.name}`}
                      value={member.role}
                      disabled={busy}
                      onChange={(e) =>
                        act(async () => {
                          await post(wsPath(workspace.id, "/members"), {
                            user_id: member.id,
                            role: e.target.value,
                          });
                          members.reload();
                        }, "Workspace role updated.")
                      }
                    >
                      <option value="member">Member</option>
                      <option value="admin">Administrator</option>
                    </select>
                  </td>
                  <td>
                    <Badge value={member.active ? "ready" : "paused"}>
                      {member.active ? "Active" : "Disabled"}
                    </Badge>
                  </td>
                  <td className="align-right">
                    <button
                      className="icon-button danger"
                      title="Remove workspace access"
                      aria-label={`Remove ${member.name}`}
                      onClick={() => setRemoving(member)}
                    >
                      <X size={17} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel audit-panel">
        <div className="panel-title">
          <div>
            <h2>Audit history</h2>
            <p>
              Administrative changes and execution outcomes, recorded as they
              happen.
            </p>
          </div>
          <ShieldCheck size={20} />
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Record</th>
                <th>Details</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {events.data?.slice(0, 30).map((event) => (
                <tr key={event.id}>
                  <td>
                    <span className="audit-action">
                      {event.action.replaceAll(".", " › ").replaceAll("_", " ")}
                    </span>
                  </td>
                  <td className="mono subtle">
                    {event.target_id?.slice(0, 8) || "—"}
                  </td>
                  <td>
                    <span className="audit-detail">
                      {JSON.stringify(event.detail) === "{}"
                        ? "—"
                        : JSON.stringify(event.detail)}
                    </span>
                  </td>
                  <td className="muted">
                    <DateLabel value={event.created_at} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {dialog === "account" && (
        <Modal title="Create a local account" close={() => setDialog(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                const created = await post<User>("/accounts", {
                  name,
                  email,
                  password,
                });
                await post(wsPath(workspace.id, "/members"), {
                  user_id: created.id,
                  role: "member",
                });
                setDialog(null);
                members.reload();
                accounts.reload();
              }, "Account created and added to this workspace.");
            }}
          >
            <label>
              Full name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                maxLength={120}
              />
            </label>
            <label>
              Email
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label>
              Initial password
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={12}
                required
                maxLength={256}
              />
            </label>
            <p className="field-help">
              Use at least 12 characters. Share the credentials with the person
              through your approved channel.
            </p>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                <UserPlus size={16} /> Create account
              </button>
            </div>
          </form>
        </Modal>
      )}
      {dialog === "member" && (
        <Modal title="Add a workspace member" close={() => setDialog(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await post(wsPath(workspace.id, "/members"), {
                  user_id: memberId,
                  role,
                });
                setDialog(null);
                members.reload();
              }, "Workspace access granted.");
            }}
          >
            <label>
              {user.is_system_admin ? "Account" : "Account ID"}
              {user.is_system_admin ? (
                <select
                  value={memberId}
                  onChange={(e) => setMemberId(e.target.value)}
                  required
                >
                  <option value="">Choose an account</option>
                  {accounts.data
                    ?.filter(
                      (a) =>
                        a.active && !members.data?.some((m) => m.id === a.id),
                    )
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} · {a.email}
                      </option>
                    ))}
                </select>
              ) : (
                <input
                  value={memberId}
                  onChange={(e) => setMemberId(e.target.value)}
                  placeholder="Account ID from your system administrator"
                  required
                />
              )}
            </label>
            <label>
              Workspace role
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="member">
                  Member — use published assistants
                </option>
                <option value="admin">
                  Administrator — manage this workspace
                </option>
              </select>
            </label>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                Grant access
              </button>
            </div>
          </form>
        </Modal>
      )}
      {dialog === "workspace" && (
        <Modal title="Create a workspace" close={() => setDialog(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await post("/workspaces", { name, description: "" });
                setDialog(null);
              }, "Workspace created. Select it from the workspace switcher.");
            }}
          >
            <label>
              Workspace name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                maxLength={120}
              />
            </label>
            <p className="muted">
              This workspace starts with its own knowledge, workflows, and
              membership list.
            </p>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                Create workspace
              </button>
            </div>
          </form>
        </Modal>
      )}
      {removing && (
        <Modal title="Remove workspace access?" close={() => setRemoving(null)}>
          <p>
            {removing.name} will immediately lose access to this workspace's
            knowledge, assistants, and run history.
          </p>
          <div className="modal-actions">
            <button className="button" onClick={() => setRemoving(null)}>
              Keep access
            </button>
            <button
              className="button destructive"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  await api(wsPath(workspace.id, `/members/${removing.id}`), {
                    method: "DELETE",
                  });
                  setRemoving(null);
                  members.reload();
                }, "Workspace access removed.")
              }
            >
              Remove access
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
