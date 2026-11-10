import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import type { CodexModelCatalog, CodexSelection, Conversation } from "../types";
import { normalizeCodexSelection } from "./codexSelection";

export function useCodexSelection(conversation?: Conversation) {
  const catalog = useQuery({
    queryKey: ["codex-models"],
    queryFn: () => api<CodexModelCatalog>("/api/conversations/models"),
    staleTime: 60_000,
  });
  const [draft, setDraft] = useState<{ conversationId?: string; selection: CodexSelection }>();
  const selection = normalizeCodexSelection(catalog.data,
    draft?.conversationId === conversation?.id ? draft?.selection : conversation?.codex_selection);
  return {
    catalog: catalog.data,
    selection,
    error: catalog.error?.message,
    onChange: (next: CodexSelection) => setDraft({ conversationId: conversation?.id, selection: next }),
  };
}

export type CodexSelectionControl = ReturnType<typeof useCodexSelection>;
