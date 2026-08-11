import type { StreamEvent } from "../api";

export type AgentActivityItem = {
  id: string;
  kind: string;
  label: string;
  detail?: string;
  status: "running" | "completed" | "failed";
};

export function updateActivities(current: AgentActivityItem[], event: StreamEvent): AgentActivityItem[] {
  if (event.type !== "activity") return current;
  const next: AgentActivityItem = {
    id: String(event.activity_id || `${event.kind || "activity"}-${current.length}`),
    kind: String(event.kind || "status"),
    label: String(event.label || "Agent 正在处理"),
    detail: event.detail ? String(event.detail) : undefined,
    status: event.status === "failed" || event.status === "completed" ? event.status : "running",
  };
  const index = current.findIndex((item) => item.id === next.id);
  if (index < 0) return [...current, next].slice(-8);
  return current.map((item, itemIndex) => itemIndex === index ? { ...item, ...next } : item);
}

export function failRunningActivities(current: AgentActivityItem[]): AgentActivityItem[] {
  return current.map((item) => item.status === "running" ? { ...item, status: "failed" } : item);
}
