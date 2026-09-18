import { useState } from "react";
import { post, wsPath } from "../api";
import { useApp, useAction, useResource } from "../ui";
import type { Answer } from "../types";
import type { GoogleConnections } from "./Connections";
export default function EmailDraft({
  draft,
  runId,
}: {
  draft: NonNullable<Answer["draft"]>;
  runId: string;
}) {
  const { workspace, user, navigate } = useApp();
  const { busy, act } = useAction();
  const key = `relay-draft-${user.id}-${runId}`;
  const [saved] = useState(() => {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null") as {
        id: string;
        draft: typeof draft;
      } | null;
    } catch {
      return null;
    }
  });
  const [value, setValue] = useState(saved?.draft || draft);
  const [to, setTo] = useState((saved?.draft.to || draft.to || []).join(", "));
  const [operation, setOperation] = useState(saved?.id || "");
  const [done, setDone] = useState(false);
  const [message, setMessage] = useState("");
  const connections = useResource<GoogleConnections>(
    wsPath(workspace.id, "/connections"),
  );
  const enabled =
    connections.data?.allowed_tools.includes("gmail_save_draft") &&
    connections.data.connections.some(
      (c) => c.provider === "gmail" && c.active && c.drafts_enabled,
    );
  return (
    <section className="agent-card">
      <h3>Review email draft</h3>
      <p>
        This email has not been sent. Review the recipients and text before
        saving.
      </p>
      <label>
        To (comma separated)
        <input
          disabled={!!operation}
          value={to}
          onChange={(e) => setTo(e.target.value)}
        />
      </label>
      <label>
        Subject
        <input
          disabled={!!operation}
          value={value.subject}
          maxLength={300}
          onChange={(e) => setValue({ ...value, subject: e.target.value })}
        />
      </label>
      <label>
        Message
        <textarea
          disabled={!!operation}
          rows={7}
          value={value.body}
          maxLength={20000}
          onChange={(e) => setValue({ ...value, body: e.target.value })}
        />
      </label>
      {enabled ? (
        <button
          className="button primary"
          disabled={busy || done}
          onClick={() =>
            act(async () => {
              const id = operation || crypto.randomUUID();
              sessionStorage.setItem(
                key,
                JSON.stringify({
                  id,
                  draft: {
                    ...value,
                    to: to
                      .split(",")
                      .map((x) => x.trim())
                      .filter(Boolean),
                  },
                }),
              );
              setOperation(id);
              const result = await post<{ status: string; message?: string }>(
                wsPath(workspace.id, "/gmail/drafts"),
                {
                  operation_id: id,
                  draft: {
                    ...value,
                    to: to
                      .split(",")
                      .map((x) => x.trim())
                      .filter(Boolean),
                  },
                },
              );
              setDone(result.status === "completed");
              setMessage(
                result.status === "completed"
                  ? "Saved to Gmail Drafts. Nothing was sent."
                  : result.message ||
                      "Save is unconfirmed. Retry to check the same operation.",
              );
            })
          }
        >
          {done
            ? "Saved to Gmail"
            : operation
              ? "Check draft save"
              : "Save draft to Gmail"}
        </button>
      ) : (
        <button className="button" onClick={() => navigate("connections")}>
          Enable Gmail draft saving in Connections
        </button>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
