import CloseRounded from "@mui/icons-material/CloseRounded";
import PsychologyRounded from "@mui/icons-material/PsychologyRounded";
import SendRounded from "@mui/icons-material/SendRounded";
import StopRounded from "@mui/icons-material/StopRounded";
import {
  Alert,
  Avatar,
  Box,
  CircularProgress,
  Drawer,
  IconButton,
  Paper,
  Stack,
  TextField,
  Typography,
  Tooltip,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, cancelTask, jsonBody, streamApi } from "../api";
import type { AgentRuntimeState, Conversation } from "../types";
import { AgentActivity } from "./AgentActivity";
import { AgentRuntimeStatus } from "./AgentRuntimeStatus";
import { activitiesFromContext, type AgentActivityItem, failRunningActivities, updateActivities } from "./agentActivityState";
import { updateAgentStateFromStream } from "./agentRuntimeState";
import { submitChatOnEnter } from "./chatInput";
import { ConversationScroller } from "./ConversationScroller";
import { activeConversationTask, isRequestAbort } from "./conversationTask";
import { MarkdownMessage } from "./MarkdownMessage";

type Props = { open: boolean; onClose: () => void; width: number; onWidthChange: (value: number) => void };

export function AgentPanel({ open, onClose, width, onWidthChange }: Props) {
  const location = useLocation();
  const queryClient = useQueryClient();
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down("md"));
  const [text, setText] = useState("");
  const [liveUser, setLiveUser] = useState("");
  const [streaming, setStreaming] = useState("");
  const [sending, setSending] = useState(false);
  const [activities, setActivities] = useState<AgentActivityItem[]>([]);
  const [error, setError] = useState("");
  const [liveTaskId, setLiveTaskId] = useState<string | null>(null);
  const [liveAgentState, setLiveAgentState] = useState<AgentRuntimeState>({});
  const [cancelling, setCancelling] = useState(false);
  const streamController = useRef<AbortController | null>(null);
  const cancellingRef = useRef(false);
  const conversation = useQuery({
    queryKey: ["global-conversation"],
    queryFn: async () => {
      const existing = await api<Conversation[]>("/api/conversations?kind=global");
      if (existing[0]) return api<Conversation>(`/api/conversations/${existing[0].id}`);
      return api<Conversation>("/api/conversations", jsonBody({ kind: "global", title: "研究助手" }));
    },
    enabled: open,
    refetchInterval: (query) => hasActiveConversationTurn(query.state.data) ? 1200 : false,
  });
  const persistedTask = activeConversationTask(conversation.data);
  const activeTaskId = liveTaskId ?? persistedTask?.id ?? null;
  const active = sending || Boolean(persistedTask);

  useEffect(() => {
    const addContext = (event: Event) => {
      const detail = (event as CustomEvent<{ context?: string }>).detail;
      if (detail?.context) setText(`请和我一起讨论这个研究节点：\n${detail.context}`);
    };
    window.addEventListener("frontier:open-agent", addContext);
    return () => window.removeEventListener("frontier:open-agent", addContext);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const content = text.trim();
    if (!content || !conversation.data || active) return;
    const controller = new AbortController();
    streamController.current = controller;
    cancellingRef.current = false;
    setText(""); setLiveUser(content); setStreaming(""); setActivities([]); setError(""); setLiveTaskId(null); setLiveAgentState({}); setSending(true);
    try {
      await streamApi(`/api/conversations/${conversation.data.id}/messages/stream`, { content, context_json: { path: location.pathname } }, (streamEvent) => {
        setLiveAgentState((value) => updateAgentStateFromStream(value, streamEvent));
        if (streamEvent.type === "meta" && typeof streamEvent.task_id === "string") setLiveTaskId(streamEvent.task_id);
        if (streamEvent.type === "delta") setStreaming((value) => value + String(streamEvent.delta ?? ""));
        if (streamEvent.type === "activity") setActivities((value) => updateActivities(value, streamEvent));
      }, controller.signal);
    } catch (cause) {
      if (!cancellingRef.current && !isRequestAbort(cause)) {
        setActivities((value) => failRunningActivities(value));
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      streamController.current = null;
      setSending(false);
      await queryClient.invalidateQueries({ queryKey: ["global-conversation"] });
      setLiveUser(""); setStreaming(""); setActivities([]); setLiveTaskId(null); setLiveAgentState({});
    }
  }

  async function stopAgent() {
    if (!activeTaskId || cancelling) return;
    cancellingRef.current = true;
    setCancelling(true);
    setError("");
    try {
      await cancelTask(activeTaskId);
      streamController.current?.abort();
      await queryClient.invalidateQueries({ queryKey: ["global-conversation"] });
    } catch (cause) {
      cancellingRef.current = false;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCancelling(false);
    }
  }

  function startResize(event: React.PointerEvent) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (next: PointerEvent) => onWidthChange(Math.max(360, Math.min(760, startWidth + startX - next.clientX)));
    const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop);
  }

  return <Drawer anchor="right" open={open} onClose={onClose} variant="temporary" ModalProps={{ keepMounted: true }} PaperProps={{ sx: { width: mobile ? "100%" : width, maxWidth: "100vw", overflow: "visible" } }}>
    {!mobile && <Box onPointerDown={startResize} aria-label="调整助手面板宽度" sx={{ position: "absolute", left: -4, top: 0, bottom: 0, width: 8, cursor: "col-resize", zIndex: 2, "&:hover": { bgcolor: "primary.light" } }} />}
    <Stack sx={{ height: "100%", minHeight: 0, overflow: "hidden" }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderBottom: 1, borderColor: "divider" }}>
        <Avatar sx={{ bgcolor: "primary.main", width: 38, height: 38 }}><PsychologyRounded fontSize="small" /></Avatar>
        <Box sx={{ flex: 1 }}><Typography fontWeight={700}>研究助手</Typography><Typography variant="caption" color="text.secondary">结合当前页面和研究资料回答</Typography></Box>
        <IconButton onClick={onClose} aria-label="关闭助手"><CloseRounded /></IconButton>
      </Stack>
      <ConversationScroller contentVersion={`${open}:${conversation.data?.messages.length ?? 0}:${liveUser.length}:${streaming.length}:${activities.map((item) => item.status).join(",")}:${sending}`}>
        {!conversation.data?.messages.length && !liveUser && <Paper sx={{ p: 2.5, bgcolor: "background.default" }}><Typography fontWeight={700}>可以从这里开始</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: .75 }}>询问近期研究进展、比较论文，或直接要求整理当前图谱。</Typography></Paper>}
        {conversation.data?.messages.map((message) => <ChatBubble key={message.id} role={message.role} content={message.content} activities={activitiesFromContext(message.context_json)} pending={message.context_json.stream_status === "queued" || message.context_json.stream_status === "running"} turnStatus={typeof message.context_json.stream_status === "string" ? message.context_json.stream_status : undefined} />)}
        {liveUser && <ChatBubble role="user" content={liveUser} />}
        {(streaming || sending || activities.length > 0) && <Box sx={{ alignSelf: "stretch" }}><AgentActivity items={activities} pending={sending && !cancelling}>{streaming ? <MarkdownMessage content={streaming} /> : undefined}</AgentActivity></Box>}
        {error && <Alert severity="error">{error}</Alert>}
      </ConversationScroller>
      <Box component="form" onSubmit={submit} sx={{ p: 2, borderTop: 1, borderColor: "divider" }}>
        <TextField fullWidth multiline maxRows={6} minRows={2} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={active ? undefined : submitChatOnEnter} placeholder={active ? "可以先输入修正后的消息，停止后重新发送" : "输入研究问题或整理指令"} InputProps={{ endAdornment: active ? <Tooltip title="停止 Agent"><span><IconButton color="error" aria-label="停止 Agent" onClick={stopAgent} disabled={!activeTaskId || cancelling} sx={{ width: 44, height: 44 }}>{cancelling ? <CircularProgress size={20} /> : <StopRounded />}</IconButton></span></Tooltip> : <IconButton color="primary" type="submit" disabled={!text.trim()}><SendRounded /></IconButton> }} />
        <AgentRuntimeStatus conversation={conversation.data} liveState={liveAgentState} />
      </Box>
    </Stack>
  </Drawer>;
}

function hasActiveConversationTurn(conversation?: Conversation): boolean { return Boolean(activeConversationTask(conversation)); }

function ChatBubble({ role, content, activities = [], pending = false, turnStatus }: { role: "user" | "assistant"; content: string; activities?: AgentActivityItem[]; pending?: boolean; turnStatus?: string }) {
  if (role === "assistant") return <Box sx={{ alignSelf: "stretch" }}><AgentActivity items={activities} pending={pending} turnStatus={turnStatus}>{content ? <MarkdownMessage content={content} /> : undefined}</AgentActivity></Box>;
  return <Box sx={{ alignSelf: "flex-end", maxWidth: "85%" }}><Paper elevation={0} sx={{ px: 1.75, py: 1.25, bgcolor: "primary.main", border: 0, color: "primary.contrastText", borderRadius: "16px 16px 4px 16px" }}><MarkdownMessage content={content} inverted /></Paper></Box>;
}
