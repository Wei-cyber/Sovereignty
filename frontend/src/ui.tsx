import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  ChevronRight,
  LoaderCircle,
  X,
} from "lucide-react";
import { api } from "./api";
import type { Configuration, User, Workspace } from "./types";

export const AppContext = createContext<{
  user: User;
  workspace: Workspace;
  config: Configuration;
  refresh: number;
  notify: (message: string, error?: boolean) => void;
  invalidate: () => void;
  navigate: (page: string) => void;
}>(null!);
export const useApp = () => useContext(AppContext);

export function useResource<T>(path: string | null, interval = 0) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((x) => x + 1), []);
  useEffect(() => {
    let cancelled = false;
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    const load = async () => {
      try {
        const result = await api<T>(path);
        if (!cancelled) {
          setData(result);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    setLoading(true);
    load();
    const timer = interval ? window.setInterval(load, interval) : undefined;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [path, revision, interval]);
  return { data, loading, error, reload };
}

export function useAction() {
  const { notify, invalidate } = useApp();
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const act = async (fn: () => Promise<void>, message?: string) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      await fn();
      invalidate();
      if (message) notify(message);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  return { busy, act };
}

export function Badge({
  value,
  children,
}: {
  value: string;
  children?: ReactNode;
}) {
  const color = [
    "completed",
    "ready",
    "published",
    "passed",
    "admin",
    "reviewed",
  ].includes(value)
    ? "green"
    : [
          "failed",
          "deleted",
          "cancelled",
          "timed_out",
          "blocked",
          "paused",
        ].includes(value)
      ? "red"
      : ["running", "processing", "queued"].includes(value)
        ? "blue"
        : "neutral";
  return (
    <span className={`badge ${color}`}>
      <span className="badge-dot" />
      {children || value.replaceAll("_", " ")}
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="header-actions">{children}</div>
    </div>
  );
}

export function Empty({
  icon,
  title,
  description,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function ErrorBox({ message }: { message?: string | null }) {
  return message ? (
    <div className="error-box" role="alert">
      <AlertCircle size={17} />
      {message}
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="loading">
      <LoaderCircle className="spin" size={20} /> Loading workspace…
    </div>
  );
}
export function DateLabel({ value }: { value: string }) {
  return (
    <time dateTime={value}>
      {new Date(value + (value.endsWith("Z") ? "" : "Z")).toLocaleDateString(
        undefined,
        { month: "short", day: "numeric" },
      )}
    </time>
  );
}
export function Duration({ ms }: { ms: number | null }) {
  return (
    <>
      {ms == null
        ? "—"
        : ms < 1000
          ? `${Math.round(ms)} ms`
          : `${(ms / 1000).toFixed(1)} s`}
    </>
  );
}
export function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current
      ?.querySelector<HTMLElement>("input,textarea,select,button")
      ?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab") {
        const nodes = ref.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), textarea, select, a[href]",
        );
        if (!nodes?.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={ref}
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={close}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
export function Submit({
  busy,
  children,
}: {
  busy: boolean;
  children: ReactNode;
}) {
  return (
    <button className="button primary" type="submit" disabled={busy}>
      {busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{" "}
      {children}
    </button>
  );
}
export function SectionLink({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="text-button" onClick={onClick}>
      {children}
      <ChevronRight size={16} />
    </button>
  );
}
export function ExternalArrow() {
  return <ArrowUpRight size={16} />;
}
