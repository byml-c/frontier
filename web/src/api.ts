const API_ROOT = import.meta.env?.VITE_API_ROOT ?? "";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `请求失败（${status}）`);
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers });
  if (!response.ok) {
    const text = await response.text();
    let detail: unknown = text || response.statusText;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      detail = body.detail ?? body;
    } catch { /* Non-JSON errors already use the response text. */ }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function jsonBody(value: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(value) };
}

export type StreamEvent = {
  type: "meta" | "delta" | "done" | "error" | string;
  delta?: string;
  content?: string;
  message?: unknown;
  task_id?: string;
  cached?: boolean;
  [key: string]: unknown;
};

export async function streamApi(
  path: string,
  payload: unknown,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_ROOT}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail ?? body);
  }
  if (!response.body) throw new ApiError(500, "浏览器没有收到流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as StreamEvent;
      onEvent(event);
      if (event.type === "error") throw new Error(String(event.message || "Agent 响应失败"));
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as StreamEvent);
}

export function formatDate(value?: string | null, withTime = false): string {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)
    ? `${value}Z`
    : value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    timeZone: "Asia/Shanghai",
    hour12: false,
  }).format(new Date(normalized));
}

export function statusLabel(status: string): string {
  return {
    candidate: "待阅读",
    ignored: "已忽略",
    reading: "精读中",
    feedback_pending: "待确认",
    completed: "已精读",
    queued: "排队中",
    running: "运行中",
    failed: "失败",
    published: "已发布",
  }[status] ?? status;
}
