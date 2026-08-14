import AccountTreeRounded from "@mui/icons-material/AccountTreeRounded";
import AddCommentRounded from "@mui/icons-material/AddCommentRounded";
import AutoFixHighRounded from "@mui/icons-material/AutoFixHighRounded";
import CheckRounded from "@mui/icons-material/CheckRounded";
import LockRounded from "@mui/icons-material/LockRounded";
import OpenInNewRounded from "@mui/icons-material/OpenInNewRounded";
import { Alert, Box, Button, CircularProgress, Divider, Stack, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { api, cancelTask, jsonBody } from "../../api";
import { Modal } from "../../components/Modal";
import { AgentActivity } from "../../components/AgentActivity";
import { ErrorState, Loading } from "../../components/States";
import { ArchitectureNodeCard, ArchitectureNodeCardEditor, type ArchitectureNodeDraft } from "./ArchitectureNodeCard";
import { ArchitectureReader, type ArchitectureReaderHandle } from "./ArchitectureReader";
import { parseArchitectureList } from "./architectureText";
import { isArchitectureScene, type ArchitectureChatCard, type ArchitectureCommand, type ArchitectureDiagnostic, type ArchitectureDocument, type ArchitectureNode, type ArchitectureReaderContext, type ArchitectureScene } from "./types";

type Props = {
  paperId: string;
  onJumpToPage: (page: number) => void;
  onContextChange?: (context: ArchitectureReaderContext) => void;
  chatCardIds?: string[];
  onAddToChat?: (card: ArchitectureChatCard) => void;
};

export type ArchitectureWorkspaceHandle = {
  execute(commands: ArchitectureCommand[]): void;
  getContext(): ArchitectureReaderContext | null;
};

const ownerId = () => {
  return globalThis.crypto?.randomUUID?.() ?? `browser-${Date.now()}-${Math.random()}`;
};

export const ArchitectureWorkspace = forwardRef<ArchitectureWorkspaceHandle, Props>(function ArchitectureWorkspace({ paperId, onJumpToPage, onContextChange, chatCardIds = [], onAddToChat }, ref) {
  const queryClient = useQueryClient();
  const readerRef = useRef<ArchitectureReaderHandle>(null);
  const editorId = useRef(ownerId());
  const editRequestId = useRef(0);
  const editingRef = useRef(false);
  const documentRef = useRef<ArchitectureDocument | null>(null);
  const selectedRef = useRef<ArchitectureNode | null>(null);
  const latestDraftRef = useRef<ArchitectureNodeDraft | null>(null);
  const lastSavedDraftRef = useRef("");
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const persistDraftRef = useRef<(value: ArchitectureNodeDraft) => Promise<void>>(() => Promise.resolve());
  const [selected, setSelected] = useState<ArchitectureNode | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ArchitectureNodeDraft | null>(null);
  const [closing, setClosing] = useState(false);
  const [diagnostics, setDiagnostics] = useState<ArchitectureDiagnostic[]>([]);
  const architecture = useQuery({
    queryKey: ["paper-architecture", paperId],
    queryFn: () => api<ArchitectureDocument>(`/api/papers/${paperId}/architecture`),
    refetchInterval: (query) => query.state.data?.status === "generating" ? 1500 : false,
  });
  const document = architecture.data;
  const scene = document?.scene;
  documentRef.current = document ?? null;
  selectedRef.current = selected;
  latestDraftRef.current = draft;
  editingRef.current = editing;

  useImperativeHandle(ref, () => ({
    execute(commands) { readerRef.current?.execute(commands); },
    getContext() { return readerRef.current?.getContext() ?? null; },
  }), []);

  const generate = useMutation({
    mutationFn: (rebuild: boolean) => api<ArchitectureDocument>(`/api/papers/${paperId}/architecture/generate`, jsonBody({ rebuild })),
    onSuccess: (value) => queryClient.setQueryData(["paper-architecture", paperId], value),
  });
  const stopGeneration = useMutation({
    mutationFn: (taskId: string) => cancelTask(taskId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["paper-architecture", paperId] }),
  });
  const acquireLock = useMutation({
    mutationFn: () => api<ArchitectureDocument>(`/api/papers/${paperId}/architecture/lock`, jsonBody({ ownerId: editorId.current })),
    onSuccess: (value) => queryClient.setQueryData(["paper-architecture", paperId], value),
  });
  const releaseLock = useMutation({
    mutationFn: () => api<ArchitectureDocument>(`/api/papers/${paperId}/architecture/lock/release`, jsonBody({ ownerId: editorId.current })),
    onSuccess: (value) => queryClient.setQueryData(["paper-architecture", paperId], value),
  });
  const saveNode = useMutation({
    mutationFn: ({ current, nextScene }: { current: ArchitectureDocument; nextScene: ArchitectureScene }) => api<ArchitectureDocument & { diagnostics?: ArchitectureDiagnostic[] }>(`/api/papers/${paperId}/architecture`, {
      method: "PUT",
      body: JSON.stringify({ expectedRevision: current.revision, ownerId: editorId.current, scene: nextScene }),
    }),
    onSuccess: (value) => {
      documentRef.current = value;
      setDiagnostics(value.diagnostics ?? []);
      queryClient.setQueryData(["paper-architecture", paperId], value);
      setSelected((current) => current ? value.scene?.nodes.find((node) => node.id === current.id) ?? null : null);
    },
  });

  useEffect(() => {
    if (!editing) return;
    const heartbeat = window.setInterval(() => {
      void api<ArchitectureDocument>(`/api/papers/${paperId}/architecture/lock`, jsonBody({ ownerId: editorId.current }))
        .then((value) => queryClient.setQueryData(["paper-architecture", paperId], value))
        .catch(() => queryClient.invalidateQueries({ queryKey: ["paper-architecture", paperId] }));
    }, 60_000);
    return () => window.clearInterval(heartbeat);
  }, [editing, paperId, queryClient]);

  useEffect(() => {
    if (!editing || !draft || closing || document?.lock?.ownerId !== editorId.current) return;
    const timer = window.setTimeout(() => { void persistDraftRef.current(draft).catch(() => undefined); }, 650);
    return () => window.clearTimeout(timer);
  }, [closing, document?.lock?.ownerId, draft, editing]);

  useEffect(() => {
    const releaseOnExit = () => {
      if (!editingRef.current) return;
      void api<ArchitectureDocument>(`/api/papers/${paperId}/architecture/lock/release`, {
        ...jsonBody({ ownerId: editorId.current }),
        keepalive: true,
      }).catch(() => undefined);
    };
    window.addEventListener("pagehide", releaseOnExit);
    return () => {
      window.removeEventListener("pagehide", releaseOnExit);
      releaseOnExit();
    };
  }, [paperId]);

  async function beginEdit(target: ArchitectureNode | null = selected) {
    if (!target || !scene) return;
    const requestId = ++editRequestId.current;
    const annotation = scene.annotations.find((item) => item.target?.type === "node" && item.target.id === target.id);
    const initialDraft = { title: target.title, symbol: target.symbol ?? "", architecture: target.type === "module" ? target.architecture ?? "" : "", keywords: target.type === "module" ? (target.keywords ?? []).join(" · ") : (target.preprocessing ?? []).join(" → "), description: target.description ?? "", annotation: annotation?.markdown ?? "" };
    latestDraftRef.current = initialDraft;
    lastSavedDraftRef.current = JSON.stringify(initialDraft);
    setDraft(initialDraft);
    setEditing(true);
    try {
      await acquireLock.mutateAsync();
    } catch {
      return;
    }
    if (editRequestId.current !== requestId) releaseLock.mutate();
  }
  function openNode(node: ArchitectureNode) {
    setSelected(node);
    setEditing(false);
    setDraft(null);
    saveNode.reset();
    acquireLock.reset();
    setClosing(false);
    void beginEdit(node);
  }
  async function closeDetail() {
    editRequestId.current += 1;
    if (acquireLock.isPending) {
      setEditing(false); setDraft(null); setSelected(null); saveNode.reset(); acquireLock.reset();
      return;
    }
    setClosing(true);
    if (documentRef.current?.lock?.ownerId === editorId.current && latestDraftRef.current) {
      try {
        await persistDraft(latestDraftRef.current);
      } catch {
        setClosing(false);
        return;
      }
      await releaseLock.mutateAsync().catch(() => undefined);
    }
    setClosing(false); setEditing(false); setDraft(null); setSelected(null); saveNode.reset(); acquireLock.reset();
  }
  function sceneWithDraft(currentScene: ArchitectureScene, target: ArchitectureNode, value: ArchitectureNodeDraft): ArchitectureScene {
    const currentNode = currentScene.nodes.find((node) => node.id === target.id) ?? target;
    const updatedNode: ArchitectureNode = currentNode.type === "module"
      ? { ...currentNode, title: value.title.trim(), symbol: value.symbol.trim() || undefined, architecture: value.architecture.trim() || undefined, keywords: parseArchitectureList(value.keywords, "module"), description: value.description.trim() || undefined }
      : { ...currentNode, title: value.title.trim(), symbol: value.symbol.trim() || undefined, preprocessing: parseArchitectureList(value.keywords, "data"), description: value.description.trim() || undefined };
    const existing = currentScene.annotations.find((item) => item.target?.type === "node" && item.target.id === target.id);
    let annotations = currentScene.annotations;
    if (value.annotation.trim()) {
      annotations = existing
        ? currentScene.annotations.map((item) => item.id === existing.id ? { ...item, markdown: value.annotation.trim() } : item)
        : [...currentScene.annotations, { id: `annotation-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`, target: { type: "node", id: target.id }, markdown: value.annotation.trim(), createdBy: "user" }];
    } else if (existing) annotations = currentScene.annotations.filter((item) => item.id !== existing.id);
    return { ...currentScene, nodes: currentScene.nodes.map((node) => node.id === target.id ? updatedNode : node), annotations };
  }
  function persistDraft(value: ArchitectureNodeDraft): Promise<void> {
    const fingerprint = JSON.stringify(value);
    if (!value.title.trim() || fingerprint === lastSavedDraftRef.current) return saveChainRef.current;
    const operation = saveChainRef.current.catch(() => undefined).then(async () => {
      if (fingerprint === lastSavedDraftRef.current) return;
      const current = documentRef.current;
      const currentScene = current?.scene;
      const target = selectedRef.current;
      if (!current || !currentScene || !target || current.lock?.ownerId !== editorId.current) throw new Error("当前编辑锁不可用");
      const valueDocument = await saveNode.mutateAsync({ current, nextScene: sceneWithDraft(currentScene, target, value) });
      documentRef.current = valueDocument;
      lastSavedDraftRef.current = fingerprint;
    });
    saveChainRef.current = operation;
    return operation;
  }
  function updateDraft(value: ArchitectureNodeDraft) {
    latestDraftRef.current = value;
    setDraft(value);
  }
  persistDraftRef.current = persistDraft;
  function resolveAsset(asset: ArchitectureScene["assets"][number]) {
    if (asset.src.startsWith("asset://architecture/")) return `/api/papers/${paperId}/architecture/assets/${encodeURIComponent(asset.src.slice("asset://architecture/".length))}`;
    if (asset.src.startsWith("asset://document/")) return `/api/papers/${paperId}/document/assets/${encodeURIComponent(asset.src.slice("asset://document/".length))}`;
    return undefined;
  }
  function addNodeToChat(node: ArchitectureNode) {
    if (!onAddToChat) return;
    const annotation = scene?.annotations
      .filter((item) => item.target?.type === "node" && item.target.id === node.id)
      .map((item) => item.markdown)
      .join("\n\n---\n\n");
    onAddToChat({
      id: node.id,
      type: node.type,
      title: node.title,
      symbol: node.symbol,
      architecture: node.type === "module" ? node.architecture : undefined,
      details: node.type === "module" ? node.keywords ?? [] : node.preprocessing ?? [],
      description: node.description,
      annotation: annotation || undefined,
    });
  }

  if (architecture.isLoading) return <Loading label="正在加载论文架构" />;
  if (architecture.error) return <ErrorState error={architecture.error} />;
  if (!document) return <ErrorState error="没有收到架构状态" />;
  if (document.status === "missing") return <EmptyArchitecture title="这篇论文还没有架构图" description="Agent 会阅读论文与必要的来源，将模型重构为统一视觉语言下的主图。" actionLabel="让 Agent 生成架构图" pending={generate.isPending} error={generate.error?.message} onAction={() => generate.mutate(false)} />;
  if (document.status === "failed" && !scene) return <EmptyArchitecture title="架构生成失败" description={document.error || "Agent 没有提交通过校验的架构。"} actionLabel="重新生成" pending={generate.isPending} error={generate.error?.message} onAction={() => generate.mutate(true)} />;
  if (document.status === "generating" && !scene) return <GeneratingArchitecture taskId={document.taskId} activities={document.progress?.activities ?? []} stopping={stopGeneration.isPending} onStop={(taskId) => stopGeneration.mutate(taskId)} />;
  if (!scene || !isArchitectureScene(scene)) return <ErrorState error="架构数据不完整，Reader 已停止渲染。" />;
  const agentLocked = document.lock?.ownerType === "agent";

  return <Box sx={{ width: "100%", height: "100%", position: "relative", overflow: "hidden" }}>
    <ArchitectureReader ref={readerRef} paperId={paperId} revision={document.revision} scene={scene} resolveAsset={resolveAsset} onNodeOpen={openNode} onContextChange={onContextChange} />
    {(document.status === "generating" || agentLocked) && <Stack spacing={1} sx={{ position: "absolute", left: 16, top: 16, zIndex: 9, width: "min(440px, calc(100% - 32px))" }}><Alert icon={<LockRounded />} severity="info" sx={{ boxShadow: 1 }} action={document.taskId ? <Button size="small" color="inherit" disabled={stopGeneration.isPending} onClick={() => stopGeneration.mutate(document.taskId!)}>停止</Button> : undefined}>Agent 正在更新架构，完成后可继续编辑。</Alert>{document.status === "generating" && <AgentActivity items={document.progress?.activities ?? []} pending />}</Stack>}
    {document.error && scene && <Alert severity="warning" sx={{ position: "absolute", left: 16, bottom: 16, zIndex: 9, maxWidth: 500 }}>{document.error}</Alert>}
    {document.stale && <Alert severity="warning" sx={{ position: "absolute", left: 16, bottom: 16, zIndex: 9, maxWidth: 500 }} action={<Button size="small" color="inherit" disabled={generate.isPending} onClick={() => generate.mutate(true)}>更新</Button>}>架构可能基于旧版论文。</Alert>}
    {diagnostics.some((item) => item.level === "warning") && <Alert severity="warning" sx={{ position: "absolute", left: 16, bottom: 16, zIndex: 9, maxWidth: 500 }}>架构已保存，但有 {diagnostics.filter((item) => item.level === "warning").length} 项布局或来源提示。</Alert>}
    <NodeDetail open={Boolean(selected)} node={selected} scene={scene} resolveAsset={resolveAsset} editing={editing} draft={draft} acquiring={acquireLock.isPending} locked={agentLocked || Boolean(document.lock && document.lock.ownerId !== editorId.current)} saving={saveNode.isPending || closing} error={saveNode.error?.message || acquireLock.error?.message} addedToChat={Boolean(selected && chatCardIds.includes(selected.id))} onAddToChat={selected && onAddToChat ? () => addNodeToChat(selected) : undefined} onClose={closeDetail} onDraft={updateDraft} onJumpToPage={onJumpToPage} />
  </Box>;
});

function EmptyArchitecture({ title, description, actionLabel, pending, error, onAction }: { title: string; description: string; actionLabel: string; pending: boolean; error?: string; onAction: () => void }) {
  return <Box sx={{ height: "100%", display: "grid", placeItems: "center", bgcolor: "#FAFBFC", p: 3 }}><Stack alignItems="center" spacing={2} sx={{ maxWidth: 520, textAlign: "center" }}><Box sx={{ width: 64, height: 64, borderRadius: "50%", bgcolor: "primary.main", color: "primary.contrastText", display: "grid", placeItems: "center" }}><AccountTreeRounded fontSize="large" /></Box><Box><Typography variant="h2">{title}</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>{description}</Typography></Box>{error && <Alert severity="error">{error}</Alert>}<Button variant="contained" startIcon={pending ? <CircularProgress size={17} color="inherit" /> : <AutoFixHighRounded />} disabled={pending} onClick={onAction}>{pending ? "正在启动" : actionLabel}</Button></Stack></Box>;
}

function GeneratingArchitecture({ taskId, activities, stopping, onStop }: { taskId?: string | null; activities: NonNullable<NonNullable<ArchitectureDocument["progress"]>["activities"]>; stopping: boolean; onStop: (taskId: string) => void }) {
  return <Box sx={{ height: "100%", overflowY: "auto", bgcolor: "#FAFBFC", p: 3 }}><Stack spacing={2} sx={{ width: "min(620px, 100%)", mx: "auto", minHeight: "100%", justifyContent: "center" }}><Box sx={{ textAlign: "center" }}><Typography variant="h2">Agent 正在生成架构图</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>这里展示真实的工具调用与 JSON 写入进度；完成校验并提交后，画布会自动出现。</Typography></Box><AgentActivity items={activities} pending />{taskId && <Button color="inherit" disabled={stopping} onClick={() => onStop(taskId)}>{stopping ? "正在停止" : "停止生成"}</Button>}</Stack></Box>;
}

function NodeDetail({ open, node, scene, resolveAsset, editing, draft, acquiring, locked, saving, error, addedToChat, onAddToChat, onClose, onDraft, onJumpToPage }: { open: boolean; node: ArchitectureNode | null; scene: ArchitectureScene; resolveAsset: (asset: ArchitectureScene["assets"][number]) => string | undefined; editing: boolean; draft: ArchitectureNodeDraft | null; acquiring: boolean; locked: boolean; saving: boolean; error?: string; addedToChat: boolean; onAddToChat?: () => void; onClose: () => void; onDraft: (value: ArchitectureNodeDraft) => void; onJumpToPage: (page: number) => void }) {
  if (!node) return null;
  const annotations = scene.annotations.filter((item) => item.target?.type === "node" && item.target.id === node.id);
  const sourceMap = new Map(scene.sources.map((source) => [source.id, source]));
  const sources = (node.sourceIds ?? []).flatMap((id) => sourceMap.get(id) ?? []);
  const primaryExample = node.type === "data" ? node.examples?.find((item) => item.id === node.primaryExampleId) : undefined;
  const asset = primaryExample?.assetId ? scene.assets.find((item) => item.id === primaryExample.assetId) : undefined;
  return <Modal open={open} onClose={onClose} title="架构节点" wide fixedHeight><Stack sx={{ height: "100%", minHeight: 0 }}>
    <Stack spacing={2} sx={{ flex: "1 1 0", minHeight: 0, overflowY: "auto", pr: .5, pb: 2 }}>
      {editing && draft ? <ArchitectureNodeCardEditor node={node} draft={draft} onDraft={onDraft} /> : <ArchitectureNodeCard node={node} assetUrl={asset ? resolveAsset(asset) : undefined} annotation={annotations.map((item) => item.markdown).join("\n\n---\n\n")} onJumpToPage={onJumpToPage} />}
      {sources.length > 0 && <><Divider /><Box><Typography variant="h3" sx={{ mb: 1 }}>来源</Typography><Stack alignItems="flex-start" spacing={.5}>{sources.map((source) => source.pageNumber ? <Button size="small" key={source.id} onClick={() => onJumpToPage(source.pageNumber!)}>第 {source.pageNumber} 页{source.label ? ` · ${source.label}` : ""}</Button> : source.url ? <Button size="small" component="a" href={source.url} target="_blank" rel="noreferrer" endIcon={<OpenInNewRounded />} key={source.id}>{source.label || source.url}</Button> : null)}</Stack></Box></>}
      {acquiring && <Alert severity="info">正在准备编辑…</Alert>}
      {locked && <Alert icon={<LockRounded />} severity="warning">Agent 或另一个页面正在更新架构；当前修改会保留在输入框中，取得编辑权后才能自动保存。</Alert>}
      {error && <Alert severity="error">{error}</Alert>}
    </Stack>
    {(editing || onAddToChat) && <><Divider /><Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1} sx={{ pt: 1.5 }}>
      {onAddToChat ? <Button color="inherit" startIcon={addedToChat ? <CheckRounded /> : <AddCommentRounded />} disabled={addedToChat} onClick={onAddToChat}>{addedToChat ? "已添加到对话" : "添加到对话"}</Button> : <Box />}
      {editing && <Typography variant="caption" color={error ? "error" : "text.secondary"}>{error ? "自动保存失败" : saving ? "正在自动保存…" : acquiring ? "正在准备自动保存…" : locked ? "等待编辑权" : "修改会自动保存"}</Typography>}
    </Stack></>}
  </Stack></Modal>;
}
