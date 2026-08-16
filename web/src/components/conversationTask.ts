import type { Conversation, Message } from "../types";

export type ActiveConversationTask = { id: string; status: "queued" | "running" };

export function activeConversationTask(conversation?: Conversation): ActiveConversationTask | null {
  if (conversation && "active_task" in conversation) {
    const authoritative = conversation.active_task;
    if (
      authoritative
      && (authoritative.status === "queued" || authoritative.status === "running")
      && authoritative.id
    ) return authoritative;
    return null;
  }
  const assistant = [...(conversation?.messages ?? [])]
    .reverse()
    .find((message) => message.role === "assistant");
  return activeTaskFromMessage(assistant);
}

function activeTaskFromMessage(message?: Message): ActiveConversationTask | null {
  const status = message?.context_json.stream_status;
  const id = message?.context_json.task_id;
  if ((status !== "queued" && status !== "running") || typeof id !== "string" || !id) return null;
  return { id, status };
}

export function isRequestAbort(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === "AbortError"
    : error instanceof Error && error.name === "AbortError";
}
