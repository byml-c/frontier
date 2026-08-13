import type { StreamEvent } from "../api";

export type AgentActivityItem = {
  id: string;
  kind: string;
  label: string;
  detail?: string;
  status: "running" | "completed" | "failed" | "cancelled";
};

export type AgentActivityPhase = {
  id: string;
  kind: "preparation" | "analysis";
  detail?: string;
  items: AgentActivityItem[];
  status: AgentActivityItem["status"];
  toolCount: number;
};

/** Remove paired strong delimiters because activity summaries are plain UI text, not Markdown. */
export function plainActivitySummary(value?: string): string {
  if (!value) return "";
  let cursor = 0;
  let result = "";
  while (cursor < value.length) {
    const start = value.indexOf("**", cursor);
    if (start < 0) return (result + value.slice(cursor)).trim();
    const end = value.indexOf("**", start + 2);
    if (end < 0) return (result + value.slice(cursor)).trim();
    result += value.slice(cursor, start) + value.slice(start + 2, end).trim();
    cursor = end + 2;
  }
  return result.trim();
}

/** Build the compact, single-line label used by activity steps. */
export function activityStepText(item: AgentActivityItem): string {
  const prefix = item.kind === "tool" ? "工具：" : "进度：";
  const detail = plainActivitySummary(item.detail);
  return `${prefix}${item.label}${detail ? ` · ${detail}` : ""}`;
}

function activityStatus(items: AgentActivityItem[]): AgentActivityItem["status"] {
  if (items.some((item) => item.status === "running")) return "running";
  if (items.some((item) => item.status === "failed")) return "failed";
  if (items.some((item) => item.status === "cancelled")) return "cancelled";
  return "completed";
}

/**
 * A reasoning event starts an analysis phase. Every following public status or
 * tool event belongs to that phase until the next reasoning event arrives.
 * Keeping this transformation separate from the component makes live and
 * persisted conversations use exactly the same presentation rules.
 */
export function groupActivities(items: AgentActivityItem[]): AgentActivityPhase[] {
  const phases: Array<{ id: string; kind: "preparation" | "analysis"; detail?: string; items: AgentActivityItem[] }> = [];
  let current: (typeof phases)[number] | undefined;

  for (const item of items) {
    if (item.kind === "reasoning") {
      current = {
        id: `analysis-${item.id}`,
        kind: "analysis",
        detail: item.detail,
        items: [item],
      };
      phases.push(current);
      continue;
    }
    if (!current) {
      current = { id: `preparation-${item.id}`, kind: "preparation", items: [] };
      phases.push(current);
    }
    current.items.push(item);
  }

  return phases.map((phase) => ({
    ...phase,
    status: activityStatus(phase.items),
    toolCount: phase.items.filter((item) => item.kind === "tool").length,
  }));
}

/** Hide the final reasoning-only phase once it has become the visible answer. */
export function activityPhasesForDisplay(
  phases: AgentActivityPhase[],
  active: boolean,
): AgentActivityPhase[] {
  const visible = phases.flatMap((phase) => {
    if (phase.kind !== "preparation") return [phase];
    const items = phase.items.filter((item) => item.kind !== "status");
    if (items.length === 0) return [];
    return [{
      ...phase,
      items,
      status: activityStatus(items),
      toolCount: items.filter((item) => item.kind === "tool").length,
    }];
  });
  if (active || visible.length === 0) return visible;
  const last = visible.at(-1);
  if (
    last?.kind === "analysis"
    && last.items.every((item) => item.kind === "reasoning")
  ) return visible.slice(0, -1);
  return visible;
}

/** Return the connection lifecycle status that belongs in the outer header. */
export function topLevelActivityStatus(items: AgentActivityItem[]): AgentActivityItem | undefined {
  const reasoningIndex = items.findIndex((item) => item.kind === "reasoning");
  const beforeReasoning = items.slice(0, reasoningIndex < 0 ? items.length : reasoningIndex);
  return beforeReasoning.find((item) => item.id === "agent-startup")
    ?? beforeReasoning.filter((item) => item.kind === "status").at(-1);
}

export function agentActivityOutcome(
  items: AgentActivityItem[],
  pending: boolean,
  turnStatus?: string,
  completionConfirmed = false,
): "running" | "completed" | "failed" | "cancelled" {
  if (pending || items.some((item) => item.status === "running")) return "running";
  if (turnStatus === "cancelled" || items.some((item) => item.status === "cancelled")) return "cancelled";
  if (turnStatus === "completed" || completionConfirmed) return "completed";
  if (
    turnStatus === "failed"
    || turnStatus === "interrupted"
    || items.some((item) => item.status === "failed")
  ) return "failed";
  return "completed";
}

export function updateActivities(
  current: AgentActivityItem[],
  event: StreamEvent,
  maxItems: number | null = null,
): AgentActivityItem[] {
  if (event.type !== "activity") return current;
  const next: AgentActivityItem = {
    id: String(event.activity_id || `${event.kind || "activity"}-${current.length}`),
    kind: String(event.kind || "status"),
    label: String(event.label || "Agent 正在处理"),
    detail: event.detail ? String(event.detail) : undefined,
    status: event.status === "failed" || event.status === "completed" || event.status === "cancelled" ? event.status : "running",
  };
  const index = current.findIndex((item) => item.id === next.id);
  if (index < 0) {
    const items = [...current, next];
    return maxItems === null ? items : items.slice(-maxItems);
  }
  return current.map((item, itemIndex) => itemIndex === index ? { ...item, ...next } : item);
}

export function failRunningActivities(current: AgentActivityItem[]): AgentActivityItem[] {
  return current.map((item) => item.status === "running" ? { ...item, status: "failed" } : item);
}

export function activitiesFromContext(context: Record<string, unknown>): AgentActivityItem[] {
  const values = Array.isArray(context.activities) ? context.activities : [];
  return values.flatMap((value) => {
    if (!value || typeof value !== "object") return [];
    const item = value as Record<string, unknown>;
    if (!item.id || !item.label) return [];
    return [{
      id: String(item.id),
      kind: String(item.kind || "status"),
      label: String(item.label),
      detail: item.detail ? String(item.detail) : undefined,
      status: item.status === "failed" || item.status === "running" || item.status === "cancelled" ? item.status : "completed",
    } satisfies AgentActivityItem];
  });
}
