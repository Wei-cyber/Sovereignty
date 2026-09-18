let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch("/api/v1" + path, {
    ...options,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    const result = await response
      .json()
      .catch(() => ({ detail: "The server could not complete this request." }));
    const detail = Array.isArray(result.detail)
      ? result.detail.map((item: { msg: string }) => item.msg).join("; ")
      : result.detail;
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}
export function post<T>(path: string, value?: unknown) {
  return api<T>(path, {
    method: "POST",
    body: value === undefined ? undefined : JSON.stringify(value),
  });
}
export const wsPath = (workspaceId: string, path = "") =>
  `/workspaces/${workspaceId}${path}`;
