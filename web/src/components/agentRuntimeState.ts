import type {
  AgentRuntime,
  AgentRuntimeState,
  AgentUsage,
  Conversation,
  ServiceCheck,
} from "../types";

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : undefined;
}

function firstString(record: UnknownRecord | undefined, keys: string[]): string | undefined {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function firstNumber(record: UnknownRecord | undefined, keys: string[]): number | undefined {
  if (!record) return undefined;
  for (const key of keys) {
    const value = record[key];
    const number = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
    if (Number.isFinite(number) && number >= 0) return number;
  }
  return undefined;
}

function hasValues(value: object | undefined): boolean {
  return Boolean(value && Object.values(value).some((item) => item !== undefined));
}

export function normalizeAgentRuntime(value: unknown): AgentRuntime | undefined {
  const record = asRecord(value);
  const runtime: AgentRuntime = {
    model: firstString(record, ["model", "model_name"]),
    reasoning_effort: firstString(record, ["reasoning_effort", "model_reasoning_effort", "effort"]),
    model_provider: firstString(record, ["model_provider", "provider"]),
  };
  return hasValues(runtime) ? runtime : undefined;
}

export function normalizeAgentUsage(value: unknown): AgentUsage | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const nestedTotal = asRecord(record.total_token_usage) ?? asRecord(record.total) ?? asRecord(record.last_token_usage);
  const inputTokens = firstNumber(record, ["input_tokens", "prompt_tokens"])
    ?? firstNumber(nestedTotal, ["input_tokens", "prompt_tokens"]);
  const outputTokens = firstNumber(record, ["output_tokens", "completion_tokens"])
    ?? firstNumber(nestedTotal, ["output_tokens", "completion_tokens"]);
  const explicitCurrent = firstNumber(record, ["current_tokens", "context_tokens", "used_tokens"])
    ?? firstNumber(nestedTotal, ["current_tokens", "context_tokens", "used_tokens"]);
  const usage: AgentUsage = {
    current_tokens: explicitCurrent ?? (inputTokens !== undefined || outputTokens !== undefined
      ? (inputTokens ?? 0) + (outputTokens ?? 0)
      : undefined),
    context_window: firstNumber(record, ["context_window", "context_window_tokens", "context_window_size", "model_context_window", "max_context_tokens"])
      ?? firstNumber(nestedTotal, ["context_window", "context_window_tokens", "context_window_size", "model_context_window", "max_context_tokens"]),
    total_tokens: firstNumber(record, ["total_tokens"])
      ?? firstNumber(nestedTotal, ["total_tokens"]),
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    cached_input_tokens: firstNumber(record, ["cached_input_tokens", "cached_tokens"])
      ?? firstNumber(nestedTotal, ["cached_input_tokens", "cached_tokens"]),
  };
  return hasValues(usage) ? usage : undefined;
}

export function agentStateFromStreamEvent(value: unknown): AgentRuntimeState {
  const event = asRecord(value);
  if (!event) return {};
  const eventType = typeof event.type === "string" ? event.type : "";
  const nestedRuntime = event.agent_runtime ?? event.runtime;
  const nestedUsage = event.agent_usage ?? event.usage ?? event.token_usage;
  const runtime = normalizeAgentRuntime(nestedRuntime)
    ?? (["runtime", "meta"].includes(eventType) ? normalizeAgentRuntime(event) : undefined);
  const usage = normalizeAgentUsage(nestedUsage)
    ?? (["usage", "meta"].includes(eventType) ? normalizeAgentUsage(event) : undefined);
  return { runtime, usage };
}

export function agentStateFromConversation(conversation?: Conversation): AgentRuntimeState {
  if (!conversation) return {};
  for (let index = conversation.messages.length - 1; index >= 0; index -= 1) {
    const message = conversation.messages[index];
    if (message.role !== "assistant") continue;
    const context = message.context_json;
    const runtime = normalizeAgentRuntime(context.agent_runtime ?? context.runtime);
    const usage = normalizeAgentUsage(context.agent_usage ?? context.usage ?? context.token_usage);
    if (runtime || usage) return { runtime, usage };
  }
  return {};
}

export function agentStateFromServiceCheck(check?: ServiceCheck | null): AgentRuntimeState {
  return {
    runtime: normalizeAgentRuntime(check),
    usage: normalizeAgentUsage(check),
  };
}

export function mergeAgentRuntimeStates(...states: AgentRuntimeState[]): AgentRuntimeState {
  const runtime = states.reduce<AgentRuntime>((result, state) => ({ ...result, ...state.runtime }), {});
  const usage = states.reduce<AgentUsage>((result, state) => ({ ...result, ...state.usage }), {});
  return {
    runtime: hasValues(runtime) ? runtime : undefined,
    usage: hasValues(usage) ? usage : undefined,
  };
}

export function updateAgentStateFromStream(previous: AgentRuntimeState, event: unknown): AgentRuntimeState {
  const incoming = agentStateFromStreamEvent(event);
  if (!incoming.runtime && !incoming.usage) return previous;
  return mergeAgentRuntimeStates(previous, incoming);
}

export function reasoningEffortLabel(value?: string): string {
  return ({ minimal: "最简", low: "低", medium: "中", high: "高", xhigh: "极高" } as Record<string, string>)[value ?? ""] ?? value ?? "未报告";
}

export type ContextUsageDetails = {
  current: number;
  window: number;
  percent: number;
  percentLabel: string;
};

export function contextUsageDetails(usage?: AgentUsage): ContextUsageDetails | undefined {
  const current = usage?.current_tokens;
  const window = usage?.context_window;
  if (current === undefined || window === undefined || window <= 0) return undefined;
  const rawPercent = current / window * 100;
  return {
    current,
    window,
    percent: Math.max(0, Math.min(100, rawPercent)),
    percentLabel: rawPercent > 0 && rawPercent < 1 ? "<1%" : `${Math.round(rawPercent)}%`,
  };
}
