import { api, post, wsPath } from "../api";
import { useApp, useResource, useAction, ErrorBox, Badge } from "../ui";
import { toolLabels } from "./Agents";
import type { components } from "../generated/api";
export type GoogleConnections = components["schemas"]["ConnectionsContract"];
// Google's Picker is loaded only after an explicit file-selection action.
declare global {
  interface Window {
    gapi: any;
    google: any;
  }
}
let pickerScript: Promise<void> | undefined;
async function loadPicker() {
  pickerScript ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://apis.google.com/js/api.js";
    s.onload = () =>
      window.gapi.load("picker", { callback: resolve, onerror: reject });
    s.onerror = reject;
    document.head.appendChild(s);
  });
  await pickerScript;
}
export default function ConnectionsPage() {
  const { workspace } = useApp();
  const state = useResource<GoogleConnections>(
    wsPath(workspace.id, "/connections"),
  );
  const { busy, act } = useAction();
  const connect = (provider: string, drafts = false) =>
    act(async () => {
      const r = await post<{ url: string }>(
        wsPath(workspace.id, "/connections/authorize"),
        { provider, drafts },
      );
      location.assign(r.url);
    });
  const files =
    state.data?.connections.find((c) => c.provider === "drive")
      ?.selected_files || [];
  const saveFiles = async (ids: string[]) => {
    await api(wsPath(workspace.id, "/connections/drive/files"), {
      method: "PUT",
      body: JSON.stringify({ file_ids: ids }),
    });
    state.reload();
  };
  const pick = () =>
    act(async () => {
      await loadPicker();
      const token = await post<{
        access_token: string;
        api_key: string;
        app_id: string;
      }>(wsPath(workspace.id, "/connections/drive/picker"));
      const p = window.google.picker;
      new p.PickerBuilder()
        .setDeveloperKey(token.api_key)
        .setAppId(token.app_id)
        .setOAuthToken(token.access_token)
        .setOrigin(location.origin)
        .enableFeature(p.Feature.MULTISELECT_ENABLED)
        .addView(
          new p.DocsView()
            .setIncludeFolders(false)
            .setMimeTypes(
              "application/vnd.google-apps.document,application/pdf,text/plain,text/markdown",
            ),
        )
        .setCallback((data: any) => {
          if (data.action === p.Action.PICKED)
            act(
              async () =>
                saveFiles(
                  [
                    ...new Set([
                      ...files.map((f) => f.id),
                      ...data.docs.map((d: any) => String(d.id)),
                    ]),
                  ].slice(0, 100),
                ),
              "Selected files saved.",
            );
        })
        .build()
        .setVisible(true);
    });
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Connections</h1>
          <p>
            Your accounts belong to you. Sharing an agent never shares your
            accounts.
          </p>
        </div>
      </div>
      <ErrorBox message={state.error} />
      {state.data && !state.data.configured && (
        <div className="onboarding-banner">
          <div>
            <strong>Google connections are unavailable</strong>
            <p>
              Your operator needs to configure the Google OAuth application.
              Existing workspace knowledge remains available.
            </p>
          </div>
        </div>
      )}
      <div className="agent-grid">
        {["gmail", "drive"].map((provider) => {
          const c = state.data?.connections.find(
            (c) => c.provider === provider,
          );
          const allowed = state.data?.allowed_tools.some((t) =>
            t.startsWith(provider + "_"),
          );
          return (
            <article className="agent-card" key={provider}>
              <h2>{provider === "gmail" ? "Gmail" : "Google Drive"}</h2>
              <Badge value={c?.active ? "ready" : "draft"}>
                {!allowed
                  ? "Disabled by administrator"
                  : c?.active
                    ? "Connected"
                    : "Not connected"}
              </Badge>
              <p>
                {c?.active
                  ? c.email
                  : provider === "gmail"
                    ? "Search and read your messages. Save unsent drafts only when you choose."
                    : "Choose individual files with Google Picker. Only selected files can supply evidence."}
              </p>
              <div className="agent-actions">
                <button
                  className="button primary"
                  disabled={busy || !state.data?.configured || !allowed}
                  onClick={() => connect(provider)}
                >
                  {c?.active ? "Reconnect" : "Connect account"}
                </button>
                {c?.active && (
                  <>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        act(async () => {
                          await post(
                            wsPath(
                              workspace.id,
                              `/connections/${provider}/test`,
                            ),
                          );
                        }, "Connection check passed.")
                      }
                    >
                      Check connection
                    </button>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        act(async () => {
                          await api(
                            wsPath(workspace.id, `/connections/${provider}`),
                            { method: "DELETE" },
                          );
                          state.reload();
                        }, "Disconnected. Account evidence is now unavailable.")
                      }
                    >
                      Disconnect
                    </button>
                  </>
                )}
                {provider === "gmail" && c?.active && !c.drafts_enabled && (
                  <button
                    className="button"
                    disabled={
                      busy ||
                      !state.data?.allowed_tools.includes("gmail_save_draft")
                    }
                    onClick={() => connect("gmail", true)}
                  >
                    Enable draft saving
                  </button>
                )}
                {provider === "drive" && c?.active && (
                  <button
                    className="button"
                    disabled={busy || !state.data?.picker_configured}
                    onClick={pick}
                  >
                    Choose files
                  </button>
                )}
              </div>
              {provider === "drive" && c?.active && (
                <>
                  <p className="muted">
                    Google Docs, text PDFs, TXT and Markdown. No folders or file
                    changes.
                  </p>
                  {!state.data?.picker_configured && (
                    <p>Google Picker setup is incomplete.</p>
                  )}
                  <ul>
                    {files.map((f) => (
                      <li key={f.id}>
                        {f.name}{" "}
                        <button
                          className="button small"
                          disabled={busy}
                          onClick={() =>
                            act(
                              () =>
                                saveFiles(
                                  files
                                    .filter((x) => x.id !== f.id)
                                    .map((x) => x.id),
                                ),
                              "File access removed.",
                            )
                          }
                        >
                          Remove access
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </article>
          );
        })}
      </div>
      {workspace.role === "admin" && state.data && (
        <section className="agent-card">
          <h2>Workspace tool policy</h2>
          <p>
            These permissions apply to every agent and connection in this
            workspace.
          </p>
          {Object.entries(toolLabels).map(([key, label]) => (
            <label className="check-row" key={key}>
              <input
                type="checkbox"
                disabled={busy}
                checked={state.data!.allowed_tools.includes(key)}
                onChange={(e) =>
                  act(async () => {
                    await api(wsPath(workspace.id, "/tool-policy"), {
                      method: "PUT",
                      body: JSON.stringify({
                        tools: e.target.checked
                          ? [...state.data!.allowed_tools, key]
                          : state.data!.allowed_tools.filter((t) => t !== key),
                      }),
                    });
                    state.reload();
                  })
                }
              />
              {label}
            </label>
          ))}
        </section>
      )}
    </div>
  );
}
