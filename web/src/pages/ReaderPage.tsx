import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import CheckRounded from "@mui/icons-material/CheckRounded";
import ChevronLeftRounded from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
import HighlightRounded from "@mui/icons-material/HighlightRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import KeyboardArrowUpRounded from "@mui/icons-material/KeyboardArrowUpRounded";
import LanguagesRounded from "@mui/icons-material/TranslateRounded";
import NoteAddRounded from "@mui/icons-material/NoteAddRounded";
import PendingActionsRounded from "@mui/icons-material/PendingActionsRounded";
import PsychologyRounded from "@mui/icons-material/PsychologyRounded";
import ReplayRounded from "@mui/icons-material/ReplayRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import ZoomInRounded from "@mui/icons-material/ZoomInRounded";
import ZoomOutRounded from "@mui/icons-material/ZoomOutRounded";
import { Alert, Box, Button, Chip, CircularProgress, FormControl, IconButton, InputAdornment, MenuItem, Paper as MuiPaper, Select, Stack, Tab, Tabs, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, cancelTask, formatDate, jsonBody, streamApi, type StreamEvent } from "../api";
import { AgentActivity } from "../components/AgentActivity";
import { AgentComposer, type AgentComposerHandle } from "../components/AgentComposer";
import { activitiesFromContext, type AgentActivityItem, failRunningActivities, updateActivities } from "../components/agentActivityState";
import { updateAgentStateFromStream } from "../components/agentRuntimeState";
import { submitChatOnEnter } from "../components/chatInput";
import { ConversationScroller } from "../components/ConversationScroller";
import { CompletionSummaryEditor } from "../components/CompletionSummaryEditor";
import { completionSummaryDraft, completionSummaryDraftIsComplete, type CompletionSummaryDraft } from "../components/completionSummary";
import { activeConversationTask, isRequestAbort } from "../components/conversationTask";
import { Modal } from "../components/Modal";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { MobileConfirmAction } from "../components/MobileConfirmAction";
import { PageHeader } from "../components/PageHeader";
import { PaperMetadataDialog } from "../components/PaperMetadataDialog";
import { PaperEvaluationToggleButtons } from "../components/PaperEvaluationToggleButtons";
import { PaperNotesPanel } from "../components/PaperNotesPanel";
import { normalizeExactName } from "../components/exactName";
import { PaperNodeSelector } from "../components/PaperNodeSelector";
import { PaperTagSelector } from "../components/PaperTagSelector";
import { ReaderModeMenu, type ReaderMode } from "../components/ReaderModeMenu";
import { caretAtPoint, offsetWithin, rangeFromOffsets, textSelectionFromRange, wordBounds } from "../components/readerSelection";
import { compactReaderControlSx, floatingReaderControlSx, readerControlSx, readerSearchFieldSx, readerSelectSx } from "../components/readerToolbarStyles";
import { ErrorState, Loading } from "../components/States";
import { TextPaperReader, type TextReaderSelection } from "../components/TextPaperReader";
import { ArchitectureMath } from "../features/architecture/ArchitectureTextView";
import { ArchitectureWorkspace, type ArchitectureWorkspaceHandle } from "../features/architecture/ArchitectureWorkspace";
import { architectureOperationIsActive } from "../features/architecture/types";
import type { ArchitectureChatCard, ArchitectureCommand, ArchitectureDocument, ArchitectureReaderContext } from "../features/architecture/types";
import type { AgentRuntimeState, Annotation, ConfirmedNodeLink, Conversation, InterestLevel, Message, NewConfirmedNode, Paper, PaperDocument, QualityLevel, ResearchNode, SuggestedNode, Tag } from "../types";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
type SelectionPosition = {
  rects?: number[][];
  block_id?: string;
  start_offset?: number;
  end_offset?: number;
  segments?: Array<{ block_id: string; start_offset: number; end_offset: number }>;
};
type SelectionState = { text: string; page: number; rect: DOMRect; anchor: { x: number; y: number }; position: SelectionPosition; pageContext: string; domRange?: { kind: "pdf"; start: number; end: number } };
type ArchitectureComposerRequest = { id: number; text: string; card: ArchitectureChatCard };
type ConversationSendOptions = { cards?: ArchitectureChatCard[]; excerpts?: Array<{ text: string; page_number: number; position: SelectionPosition }>; clearComposer?: boolean; clearPendingReferences?: boolean };
const HIGHLIGHT_COLORS = ["#F3D76B", "#A8D8A8", "#9CCCF4", "#F3B1C8", "#D4B5F5"];
const DEFAULT_HIGHLIGHT_COLOR = "#F3D76B";
const isReadingNote = (item: Annotation) => item.kind === "note" || item.kind === "highlight";
const selectionKey = (item: SelectionState) => `${item.page}:${item.text}:${item.position.segments?.map((segment) => `${segment.block_id}:${segment.start_offset}:${segment.end_offset}`).join("|") || item.position.block_id || item.position.rects?.flat().join(":") || "text"}`;

export function ReaderPage() {
  const { paperId = "" } = useParams(); const navigate = useNavigate(); const location = useLocation(); const queryClient = useQueryClient(); const theme = useTheme(); const mobile = useMediaQuery(theme.breakpoints.down("md"));
  const architectureMode = location.pathname.endsWith("/architecture");
  const scrollRef = useRef<HTMLDivElement>(null); const architectureRef = useRef<ArchitectureWorkspaceHandle>(null); const saveTimer = useRef<number | undefined>(undefined); const mobileSelectionTimer = useRef<number | undefined>(undefined); const mobileSelectionStart = useRef<{ x: number; y: number } | null>(null);
  const [pageCount, setPageCount] = useState(0); const [currentPage, setCurrentPage] = useState(1); const [width, setWidth] = useState(760); const [zoom, setZoom] = useState(1); const [searchMatchIndex, setSearchMatchIndex] = useState(0);
  const [readerMode, setReaderMode] = useState<"text" | "pdf">(() => window.matchMedia("(max-width: 899px)").matches ? "text" : "pdf"); const [mobileView, setMobileView] = useState("reader"); const [readerToolsOpen, setReaderToolsOpen] = useState(false); const [sideTab, setSideTab] = useState("assistant"); const [sideWidth, setSideWidth] = useState(() => Number(localStorage.getItem("frontier-reader-side-width")) || 460);
  const [selection, setSelection] = useState<SelectionState | null>(null); const [chatSelections, setChatSelections] = useState<SelectionState[]>([]); const [editingNote, setEditingNote] = useState<Annotation | null>(null); const [search, setSearch] = useState(""); const [feedbackOpen, setFeedbackOpen] = useState(false); const [metadataOpen, setMetadataOpen] = useState(false); const [pdfError, setPdfError] = useState(""); const [finishActivities, setFinishActivities] = useState<AgentActivityItem[]>([]); const [finishing, setFinishing] = useState(false); const [finishError, setFinishError] = useState("");
  const [architectureContext, setArchitectureContext] = useState<ArchitectureReaderContext | null>(null); const [pendingArchitectureCommands, setPendingArchitectureCommands] = useState<ArchitectureCommand[]>([]); const [chatArchitectureCards, setChatArchitectureCards] = useState<ArchitectureChatCard[]>([]); const [architectureComposerRequest, setArchitectureComposerRequest] = useState<ArchitectureComposerRequest | null>(null); const [architectureAgentBusy, setArchitectureAgentBusy] = useState(false);
  const paper = useQuery({ queryKey: ["paper", paperId], queryFn: () => api<Paper>(`/api/papers/${paperId}`) });
  const architectureStatus = useQuery({
    queryKey: ["paper-architecture", paperId],
    queryFn: () => api<ArchitectureDocument>(`/api/papers/${paperId}/architecture`),
    refetchInterval: (query) => {
      return architectureOperationIsActive(query.state.data) ? 1500 : false;
    },
  });
  const architectureGenerationActive = architectureOperationIsActive(architectureStatus.data);
  const progress = useQuery({ queryKey: ["progress", paperId], queryFn: () => api<{ page_number: number; zoom: number }>(`/api/papers/${paperId}/progress`) });
  const annotations = useQuery({ queryKey: ["annotations", paperId], queryFn: () => api<Annotation[]>(`/api/papers/${paperId}/annotations`) });
  const textDocument = useQuery({ queryKey: ["paper-document", paperId], queryFn: () => api<PaperDocument>(`/api/papers/${paperId}/document`), enabled: !architectureMode && readerMode === "text", refetchInterval: (query) => query.state.data?.status === "processing" ? 2000 : false });
  const rebuildDocument = useMutation({ mutationFn: () => api(`/api/papers/${paperId}/document/rebuild`, { method: "POST" }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["paper-document", paperId] }) });
  const pdfText = useQuery({ queryKey: ["pdf-text", paperId], queryFn: () => api<{ pages: Array<{ page_number: number; text: string }> }>(`/api/papers/${paperId}/text`), enabled: !architectureMode && search.length > 1 });
  const createAnnotation = useMutation({ mutationFn: (payload: Record<string, unknown>) => api<Annotation>(`/api/papers/${paperId}/annotations`, jsonBody(payload)), onSuccess: () => { setSelection(null); window.getSelection()?.removeAllRanges(); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); } });
  const saveTranslatedNote = useMutation({
    mutationFn: ({ current, saved, content }: { current: SelectionState; saved?: Annotation; content: string }) => {
      if (saved) {
        const nextContent = [saved.content?.trim(), content.trim()].filter(Boolean).join("\n\n");
        return api<Annotation>(`/api/papers/${paperId}/annotations/${saved.id}`, {
          method: "PATCH",
          body: JSON.stringify({ kind: "note", page_number: saved.page_number, selected_text: saved.selected_text, content: nextContent, position_json: saved.position_json, color: saved.color || DEFAULT_HIGHLIGHT_COLOR }),
        });
      }
      return api<Annotation>(`/api/papers/${paperId}/annotations`, jsonBody({ kind: "note", page_number: current.page, selected_text: current.text, content: content.trim(), position_json: current.position, color: DEFAULT_HIGHLIGHT_COLOR }));
    },
    onSuccess: () => { setSelection(null); window.getSelection()?.removeAllRanges(); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); },
  });
  const updateAnnotation = useMutation({
    mutationFn: ({ note, content, color }: { note: Annotation; content: string; color: string }) => api<Annotation>(`/api/papers/${paperId}/annotations/${note.id}`, {
      method: "PATCH",
      body: JSON.stringify({ kind: "note", page_number: note.page_number, selected_text: note.selected_text, content, position_json: note.position_json, color }),
    }),
    onSuccess: () => { setEditingNote(null); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); },
  });
  const deleteAnnotation = useMutation({ mutationFn: (id: string) => api(`/api/papers/${paperId}/annotations/${id}`, { method: "DELETE" }), onSuccess: () => { setEditingNote(null); setSelection(null); window.getSelection()?.removeAllRanges(); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); } });
  const saveProgress = useMutation({ mutationFn: (payload: { page_number: number; zoom: number }) => api(`/api/papers/${paperId}/progress`, { method: "PUT", body: JSON.stringify({ ...payload, scroll_offset: 0 }) }) });
  useEffect(() => { api(`/api/papers/${paperId}/start-reading`, { method: "POST" }).catch(() => undefined); }, [paperId]);
  useEffect(() => { setChatArchitectureCards([]); setArchitectureContext(null); }, [paperId]);
  useEffect(() => { if (progress.data) { const savedPage = progress.data.page_number; setCurrentPage(savedPage); setZoom(progress.data.zoom); if (pageCount > 0) window.setTimeout(() => scrollRef.current?.querySelector(`[data-page-number="${savedPage}"]`)?.scrollIntoView({ behavior: "auto", block: "start" }), 50); } }, [progress.data, pageCount]);
  useEffect(() => { const element = scrollRef.current; if (!element) return; const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.min(920, entry.contentRect.width - 32)))); observer.observe(element); return () => observer.disconnect(); }, [architectureMode, paper.data?.id, mobileView, readerMode]);
  useEffect(() => { if (paper.data?.status === "feedback_pending") setFeedbackOpen(true); }, [paper.data?.status]);
  useEffect(() => { if (!architectureMode || !pendingArchitectureCommands.length) return; const timer = window.setTimeout(() => { architectureRef.current?.execute(pendingArchitectureCommands); setPendingArchitectureCommands([]); }, 80); return () => window.clearTimeout(timer); }, [architectureMode, pendingArchitectureCommands]);
  const searchMatches = useMemo(() => search.trim().length > 1 ? pdfText.data?.pages.filter((page) => page.text.toLowerCase().includes(search.trim().toLowerCase())) ?? [] : [], [pdfText.data, search]);
  useEffect(() => { setSearchMatchIndex(0); }, [search]);
  const noteItems = annotations.data?.filter(isReadingNote) ?? [];
  const matchingNote = selection ? noteItems.find((item) => {
    if (item.page_number !== selection.page || item.selected_text?.trim() !== selection.text.trim()) return false;
    if (selection.position.block_id) return item.position_json.block_id === selection.position.block_id && item.position_json.start_offset === selection.position.start_offset;
    const saved = item.position_json?.rects?.[0];
    const current = selection.position.rects?.[0];
    if (!saved || !current) return false;
    return Math.abs(saved[0] - current[0]) < 0.015
      && Math.abs(saved[1] - current[1]) < 0.015
      && Math.abs(saved[2] - current[2]) < 0.015
      && Math.abs(saved[3] - current[3]) < 0.015;
  }) : undefined;
  function dismissSelection() { setSelection(null); window.getSelection()?.removeAllRanges(); }
  function returnToPreviousPage() {
    const historyIndex = Number(window.history.state?.idx ?? 0);
    if (historyIndex > 0) navigate(-1);
    else navigate("/library", { replace: true });
  }
  function changeMobileView(value: string) { setMobileView(value); if (value !== "reader") setReaderToolsOpen(false); }
  function editNote(note: Annotation) { updateAnnotation.reset(); setEditingNote(note); setSideTab("notes"); if (mobile) changeMobileView("notes"); }
  function selectReaderMode(mode: ReaderMode) { setMobileView("reader"); setReaderToolsOpen(false); dismissSelection(); if (mode === "architecture") { if (!architectureMode) navigate(`/paper/${paperId}/architecture`); return; } setReaderMode(mode); if (architectureMode) navigate(`/paper/${paperId}`); }
  function handleArchitectureCommands(commands: ArchitectureCommand[]) { setPendingArchitectureCommands(commands); selectReaderMode("architecture"); }
  function addArchitectureCard(card: ArchitectureChatCard) { setChatArchitectureCards((current) => current.some((item) => item.id === card.id) ? current : [...current, card]); if (mobile) setMobileView("assistant"); else setSideTab("assistant"); }
  function requestArchitectureExpansion(card: ArchitectureChatCard) { if (mobile) setMobileView("assistant"); else setSideTab("assistant"); setArchitectureComposerRequest({ id: Date.now(), text: `请展开架构节点“${card.title}”，补充其内部模块、数据流和必要的 Tensor 信息。`, card }); }
  function jumpFromArchitecture(page: number) { selectReaderMode("pdf"); setCurrentPage(page); window.setTimeout(() => scrollRef.current?.querySelector(`[data-page-number="${page}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 120); }
  function captureTextSelection(value: TextReaderSelection) { setSelection({ text: value.text, page: value.page, rect: value.rect, anchor: value.anchor, position: { block_id: value.blockId, start_offset: value.startOffset, end_offset: value.endOffset, segments: value.segments.map((segment) => ({ block_id: segment.blockId, start_offset: segment.startOffset, end_offset: segment.endOffset })) }, pageContext: value.pageContext }); }
  function addSelectionToChat() { if (!selection) return; setChatSelections((current) => current.some((item) => selectionKey(item) === selectionKey(selection)) ? current : [...current, selection]); setSideTab("assistant"); if (mobile) changeMobileView("assistant"); dismissSelection(); }
  useEffect(() => { if (!selection) return; const closeOutside = (event: PointerEvent) => { const target = event.target as Element | null; if (target?.closest("[data-selection-toolbar], [data-selection-handle]")) return; if (mobile) return; const nativeSelection = window.getSelection(); const inside = nativeSelection?.rangeCount ? [...nativeSelection.getRangeAt(0).getClientRects()].some((rect) => event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) : false; if (!inside) dismissSelection(); }; document.addEventListener("pointerdown", closeOutside); return () => document.removeEventListener("pointerdown", closeOutside); }, [mobile, selection]);
  const jumpTo = useCallback((page: number, smooth = true) => { if (mobile) { setReaderMode("pdf"); setMobileView("reader"); } window.setTimeout(() => { scrollRef.current?.querySelector(`[data-page-number="${page}"]`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }); }, 30); setCurrentPage(page); }, [mobile]);
  function jumpToSearchMatch(nextIndex: number) {
    if (!searchMatches.length) return;
    const index = (nextIndex + searchMatches.length) % searchMatches.length;
    setSearchMatchIndex(index);
    const page = searchMatches[index].page_number;
    jumpTo(page);
    let attempts = 0;
    const reveal = () => {
      const mark = scrollRef.current?.querySelector(`[data-page-number="${page}"] .pdf-search-mark`);
      if (mark) mark.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
      else if (attempts++ < 10) window.setTimeout(reveal, 100);
    };
    window.setTimeout(reveal, 80);
  }
  function onScroll() { window.clearTimeout(saveTimer.current); saveTimer.current = window.setTimeout(() => { const pages = [...(scrollRef.current?.querySelectorAll<HTMLElement>("[data-page-number]") ?? [])]; const center = window.innerHeight / 2; const closest = pages.sort((a, b) => Math.abs(a.getBoundingClientRect().top - center) - Math.abs(b.getBoundingClientRect().top - center))[0]; const page = Number(closest?.dataset.pageNumber || currentPage); setCurrentPage(page); saveProgress.mutate({ page_number: page, zoom }); }, 220); }
  function captureSelection(anchor?: { x: number; y: number }) { const current = window.getSelection(); const text = current?.toString().trim(); if (!current || !text || current.rangeCount === 0) return; const range = current.getRangeAt(0); const parent = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE ? range.commonAncestorContainer as Element : range.commonAncestorContainer.parentElement; const pageElement = parent?.closest("[data-page-number]") as HTMLElement | null; const textLayer = pageElement?.querySelector<HTMLElement>(".react-pdf__Page__textContent"); if (!pageElement || !textLayer || !textLayer.contains(range.startContainer) || !textLayer.contains(range.endContainer)) return; const offsets = rangeOffsets(textLayer, range); if (!offsets) return; const next = pdfSelectionFromRange(range, pageElement, textLayer, anchor); if (!next) return; setSelection({ ...next, domRange: { kind: "pdf", ...offsets } }); if (mobile) window.setTimeout(() => current.removeAllRanges(), 0); }
  function beginMobilePdfSelection(event: React.PointerEvent<HTMLDivElement>) {
    if (!mobile || readerMode !== "pdf" || event.pointerType === "mouse" || (event.target as Element).closest("button, input, [role=button]")) return;
    window.clearTimeout(mobileSelectionTimer.current); mobileSelectionStart.current = { x: event.clientX, y: event.clientY };
    mobileSelectionTimer.current = window.setTimeout(() => {
      const point = caretAtPoint(event.clientX, event.clientY); const element = point && (point.node.nodeType === Node.ELEMENT_NODE ? point.node as Element : point.node.parentElement);
      const textLayer = element?.closest<HTMLElement>(".react-pdf__Page__textContent"); const page = textLayer?.closest<HTMLElement>(".pdf-page");
      if (!point || !textLayer || !page) return;
      const offset = offsetWithin(textLayer, point.node, point.offset); if (offset == null) return;
      const bounds = wordBounds(textLayer.textContent || "", offset); const range = rangeFromOffsets(textLayer, bounds.start, bounds.end); if (!range) return;
      const next = pdfSelectionFromRange(range, page, textLayer); if (!next) return;
      setSelection({ ...next, domRange: { kind: "pdf", start: bounds.start, end: bounds.end } }); navigator.vibrate?.(12);
    }, 420);
  }
  function trackMobilePdfSelection(event: React.PointerEvent<HTMLDivElement>) { const start = mobileSelectionStart.current; if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) cancelMobilePdfSelection(); }
  function cancelMobilePdfSelection() { window.clearTimeout(mobileSelectionTimer.current); mobileSelectionStart.current = null; }
  async function finishReading() {
    setFinishing(true);
    setFinishActivities([]);
    setFinishError("");
    setFeedbackOpen(true);
    try {
      await streamApi("/api/tasks/stream", { kind: "finish_reading", payload_json: { paper_id: paperId } }, (event) => {
        if (event.type === "activity") setFinishActivities((value) => updateActivities(value, event, null));
      });
      await queryClient.invalidateQueries({ queryKey: ["paper", paperId] });
    } catch (cause) {
      setFinishActivities((value) => failRunningActivities(value));
      setFinishError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setFinishing(false);
    }
  }
  function startResize(event: React.PointerEvent) { event.preventDefault(); const startX = event.clientX; const initial = sideWidth; const move = (next: PointerEvent) => { const value = Math.max(360, Math.min(760, initial + startX - next.clientX)); setSideWidth(value); localStorage.setItem("frontier-reader-side-width", String(value)); }; const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); }; window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop); }
  if (paper.isLoading) return <Loading label="正在打开论文" />; if (paper.error) return <ErrorState error={paper.error} />; const item = paper.data!;
  const activeReaderMode: ReaderMode = architectureMode ? "architecture" : readerMode;
  const modeControl = <ReaderModeMenu value={activeReaderMode} compact={mobile} onChange={selectReaderMode} />;
  const toolbarModeControl = mobile ? undefined : modeControl;
  return <Box sx={{ height: "100%", display: "flex", flexDirection: "column", bgcolor: "#EEEEEE", overflow: "hidden" }}>
    <PageHeader
      eyebrow={`论文阅读 · ${item.first_author || "作者信息待确认"} · ${formatDate(item.published_at)}`}
      title={item.title}
      actions={<><Tooltip title="返回上一页"><IconButton onClick={returnToPreviousPage} aria-label="返回上一页" sx={{ width: 36, height: 36 }}><ArrowBackRounded /></IconButton></Tooltip><Tooltip title="查看和编辑论文信息"><IconButton onClick={() => setMetadataOpen(true)} aria-label="论文信息" sx={{ width: 36, height: 36 }}><EditNoteRounded /></IconButton></Tooltip>{item.status === "feedback_pending" ? <><MobileConfirmAction label="待确认精读结论" icon={<PendingActionsRounded fontSize="small" />} color="warning" onConfirm={() => setFeedbackOpen(true)} /><Button size="small" variant="outlined" color="warning" onClick={() => setFeedbackOpen(true)} sx={{ display: { xs: "none", md: "inline-flex" } }}>待确认</Button></> : item.status !== "completed" && <><MobileConfirmAction label={finishing ? "正在整理精读结论" : "完成精读"} icon={finishing ? <CircularProgress size={17} /> : <CheckRounded fontSize="small" />} onConfirm={finishReading} disabled={finishing} /><Button size="small" variant="contained" startIcon={finishing ? <CircularProgress size={16} color="inherit" /> : <CheckRounded />} onClick={finishReading} disabled={finishing} sx={{ display: { xs: "none", md: "inline-flex" } }}>{finishing ? "正在整理" : "完成精读"}</Button></>}</>}
    />
    {mobile && <Stack direction="row" alignItems="center" sx={{ bgcolor: "background.paper", borderBottom: 1, borderColor: "divider", pl: 1 }}><Box sx={{ flexShrink: 0, display: "grid", placeItems: "center" }}>{modeControl}</Box><Tabs value={mobileView} onChange={(_, value) => changeMobileView(value)} variant="fullWidth" sx={{ minHeight: 40, flex: 1, "& .MuiTab-root": { minHeight: 40, py: 0, px: .5, minWidth: 0, fontSize: ".82rem" } }}><Tab value="reader" label={<Stack component="span" direction="row" alignItems="center" spacing={.15}>阅读{!architectureMode && <KeyboardArrowDownRounded sx={{ fontSize: 17, transition: "transform .18s ease", transform: readerToolsOpen ? "rotate(180deg)" : "rotate(0deg)" }} />}</Stack>} aria-expanded={!architectureMode ? readerToolsOpen : undefined} onClick={() => { if (!architectureMode && mobileView === "reader") setReaderToolsOpen((open) => !open); }} /><Tab value="assistant" label="问答" /><Tab value="notes" label={`笔记 ${noteItems.length}`} /></Tabs></Stack>}
    {!architectureMode && architectureGenerationActive && <Alert severity="info" action={<Button size="small" color="inherit" onClick={() => selectReaderMode("architecture")}>查看进度</Button>}>Agent 正在后台生成架构，切换阅读模式不会中断任务。</Alert>}
    <Box sx={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden", "& > :last-child": { minHeight: 0, overflow: "hidden", ...(mobile && mobileView === "reader" ? { display: "none" } : {}) } }}>
      <Box sx={{ flex: 1, minWidth: 0, position: "relative", display: mobile && mobileView !== "reader" ? "none" : "flex", flexDirection: "column" }}>{architectureMode ? <>{!mobile && <Box sx={{ position: "absolute", zIndex: 10, top: 12, left: 12 }}>{modeControl}</Box>}<ArchitectureWorkspace ref={architectureRef} paperId={paperId} onJumpToPage={jumpFromArchitecture} onContextChange={setArchitectureContext} chatCardIds={chatArchitectureCards.map((card) => card.id)} onAddToChat={addArchitectureCard} onRequestExpand={requestArchitectureExpansion} onAgentBusyChange={setArchitectureAgentBusy} /></> : <>{readerMode === "text" ? <TextPaperReader document={textDocument.data} annotations={noteItems} mobile={mobile} toolsOpen={!mobile || readerToolsOpen} activeSelection={selection?.position.block_id ? { blockId: selection.position.block_id, startOffset: selection.position.start_offset || 0, endOffset: selection.position.end_offset || 0, segments: selection.position.segments?.map((segment) => ({ blockId: segment.block_id, startOffset: segment.start_offset, endOffset: segment.end_offset })) } : undefined} onRetry={() => rebuildDocument.mutate()} modeControl={toolbarModeControl} onUsePdf={() => selectReaderMode("pdf")} onSelection={captureTextSelection} /> : <>{(!mobile || readerToolsOpen) && <PdfToolbar mobile={mobile} currentPage={currentPage} pageCount={pageCount} zoom={zoom} search={search} matches={searchMatches.length} matchIndex={searchMatchIndex} onSearch={setSearch} onSearchMatch={jumpToSearchMatch} onJump={jumpTo} onZoom={setZoom} modeControl={toolbarModeControl} />}<Box className={mobile ? "pdf-scroll mobile-reader-selection" : "pdf-scroll desktop-reader-selection"} ref={scrollRef} onContextMenu={mobile ? (event) => event.preventDefault() : undefined} onPointerDown={beginMobilePdfSelection} onPointerMove={trackMobilePdfSelection} onPointerUp={cancelMobilePdfSelection} onPointerCancel={cancelMobilePdfSelection} onScroll={onScroll} onMouseUp={!mobile ? (event) => captureSelection({ x: event.clientX, y: event.clientY }) : undefined}><Document file={`/api/papers/${paperId}/pdf?v=${encodeURIComponent(item.pdf_version || "current")}`} onLoadSuccess={({ numPages }) => { setPageCount(numPages); setPdfError(""); }} onLoadError={(error) => setPdfError(error.message)} loading={<Loading label="正在加载 PDF" />} error={<ErrorState error={pdfError || "PDF 无法加载，请检查源文件。"} />}>{Array.from({ length: pageCount }, (_, index) => <LazyPdfPage key={index + 1} pageNumber={index + 1} pageWidth={width * zoom} scrollRoot={scrollRef} items={annotations.data?.filter((entry) => entry.page_number === index + 1) ?? []} activeSelection={mobile && selection?.page === index + 1 && selection.domRange?.kind === "pdf" ? selection.position.rects : undefined} search={search} />)}</Document></Box></>}{selection && (mobile || readerMode === "text") && <SelectionHandles selection={selection} mode={readerMode} onChange={setSelection} />}{selection && <SelectionToolbar mobile={mobile} selection={selection} paperId={paperId} note={matchingNote} onClose={dismissSelection} onSaveNote={(content, color) => createAnnotation.mutate({ kind: "note", page_number: selection.page, selected_text: selection.text, content, position_json: selection.position, color })} onSaveTranslationNote={(content) => saveTranslatedNote.mutateAsync({ current: selection, saved: matchingNote, content }).then(() => undefined)} onEditNote={() => { if (matchingNote) editNote(matchingNote); dismissSelection(); }} onAddToChat={addSelectionToChat} />}</>}</Box>
      {!mobile && <Box onPointerDown={startResize} sx={{ width: 7, cursor: "col-resize", bgcolor: "divider", "&:hover": { bgcolor: "primary.light" } }} />}
      <Box sx={{ width: mobile ? "100%" : sideWidth, minWidth: 0, display: mobile && mobileView === "pdf" ? "none" : "flex", flexDirection: "column", bgcolor: "background.paper" }}><Tabs value={mobile ? mobileView : sideTab} onChange={(_, value) => mobile ? setMobileView(value) : setSideTab(value)} sx={{ borderBottom: 1, borderColor: "divider", display: mobile ? "none" : "flex" }}><Tab value="assistant" label="论文问答" /><Tab value="notes" label={`笔记（${noteItems.length}）`} /></Tabs>{(mobile ? mobileView : sideTab) === "assistant" ? <PaperConversation paperId={paperId} onJump={architectureMode ? jumpFromArchitecture : jumpTo} selections={chatSelections} onRemoveSelection={(key) => setChatSelections((current) => current.filter((item) => selectionKey(item) !== key))} onClearSelections={() => setChatSelections([])} architectureCards={chatArchitectureCards} onRemoveArchitectureCard={(id) => setChatArchitectureCards((current) => current.filter((item) => item.id !== id))} onClearArchitectureCards={() => setChatArchitectureCards([])} composerRequest={architectureComposerRequest} externalBusy={architectureGenerationActive || (architectureMode && architectureAgentBusy)} architectureContext={architectureMode ? architectureContext : null} onArchitectureCommands={handleArchitectureCommands} onArchitectureSceneUpdated={() => queryClient.invalidateQueries({ queryKey: ["paper-architecture", paperId] })} /> : <PaperNotesPanel notes={noteItems} editingId={editingNote?.id ?? null} saving={updateAnnotation.isPending} saveError={updateAnnotation.error?.message} onJump={architectureMode ? jumpFromArchitecture : jumpTo} onEdit={editNote} onCancelEdit={() => { updateAnnotation.reset(); setEditingNote(null); }} onSave={(note, content, color) => updateAnnotation.mutate({ note, content, color })} onDelete={(note) => deleteAnnotation.mutate(note.id)} />}</Box>
    </Box><FeedbackModal open={feedbackOpen} paper={item} processing={finishing} activities={finishActivities} processError={finishError} onClose={() => setFeedbackOpen(false)} onSaved={() => { setFeedbackOpen(false); queryClient.invalidateQueries({ queryKey: ["paper", paperId] }); }} /><PaperMetadataDialog paperId={paperId} open={metadataOpen} onClose={() => setMetadataOpen(false)} />
  </Box>;
}

type PdfToolbarProps = { mobile: boolean; currentPage: number; pageCount: number; zoom: number; search: string; matches: number; matchIndex: number; onSearch: (value: string) => void; onSearchMatch: (index: number) => void; onJump: (page: number) => void; onZoom: (value: number) => void; modeControl?: ReactNode };
function PdfSearchField({ search, matches, matchIndex, onSearch, onSearchMatch, compact = false }: Pick<PdfToolbarProps, "search" | "matches" | "matchIndex" | "onSearch" | "onSearchMatch"> & { compact?: boolean }) { return <TextField fullWidth size="small" value={search} onChange={(event) => onSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); onSearchMatch(matchIndex + (event.shiftKey ? -1 : 1)); } }} placeholder="搜索全文" sx={readerSearchFieldSx} InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded sx={{ fontSize: compact ? 18 : 20 }} /></InputAdornment>, endAdornment: <InputAdornment position="end"><Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap", mr: "10px" }}>{matches ? matchIndex + 1 : 0}/{matches}</Typography><IconButton disabled={!matches} aria-label="上一个匹配页" onClick={() => onSearchMatch(matchIndex - 1)} sx={compactReaderControlSx}><KeyboardArrowUpRounded fontSize="small" /></IconButton><IconButton disabled={!matches} aria-label="下一个匹配页" onClick={() => onSearchMatch(matchIndex + 1)} sx={compactReaderControlSx}><KeyboardArrowDownRounded fontSize="small" /></IconButton></InputAdornment> }} />; }
function PageSelect({ currentPage, pageCount, onJump }: Pick<PdfToolbarProps, "currentPage" | "pageCount" | "onJump">) { const total = Math.max(1, pageCount); const value = Math.min(Math.max(1, currentPage), total); return <FormControl size="small"><Select value={value} onChange={(event) => onJump(Number(event.target.value))} aria-label="PDF 页码" renderValue={(page) => `第 ${page} / ${pageCount || "—"} 页`} sx={{ ...readerSelectSx, minWidth: 112 }}>{Array.from({ length: total }, (_, index) => index + 1).map((page) => <MenuItem value={page} key={page}>第 {page} 页</MenuItem>)}</Select></FormControl>; }
function PdfToolbar({ mobile, currentPage, pageCount, zoom, search, matches, matchIndex, onSearch, onSearchMatch, onJump, onZoom, modeControl }: PdfToolbarProps) {
  const pageControls = <Stack direction="row" justifyContent="flex-end" alignItems="center" spacing={mobile ? .5 : 1}><IconButton aria-label="上一页" onClick={() => onJump(Math.max(1, currentPage - 1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ChevronLeftRounded fontSize={mobile ? "small" : "medium"} /></IconButton><PageSelect currentPage={currentPage} pageCount={pageCount} onJump={onJump} /><IconButton aria-label="下一页" onClick={() => onJump(Math.min(pageCount || 1, currentPage + 1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ChevronRightRounded fontSize={mobile ? "small" : "medium"} /></IconButton><IconButton aria-label="缩小" onClick={() => onZoom(Math.max(.7, zoom - .1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ZoomOutRounded fontSize={mobile ? "small" : "medium"} /></IconButton><Typography variant="caption" sx={{ minWidth: mobile ? 34 : 38, textAlign: "center" }}>{Math.round(zoom * 100)}%</Typography><IconButton aria-label="放大" onClick={() => onZoom(Math.min(1.8, zoom + .1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ZoomInRounded fontSize={mobile ? "small" : "medium"} /></IconButton></Stack>;
  if (mobile) return <Box sx={{ position: "absolute", zIndex: 6, top: 0, left: 0, right: 0, display: "grid", gridTemplateColumns: modeControl ? "auto minmax(0, 1fr)" : "minmax(0, 1fr)", gridTemplateAreas: modeControl ? '"search search" "mode controls"' : '"search" "controls"', gap: 1, alignItems: "center", p: 1, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider", boxShadow: 2 }}><Box sx={{ gridArea: "search", minWidth: 0 }}><PdfSearchField compact search={search} matches={matches} matchIndex={matchIndex} onSearch={onSearch} onSearchMatch={onSearchMatch} /></Box>{modeControl && <Box sx={{ gridArea: "mode" }}>{modeControl}</Box>}<Box sx={{ gridArea: "controls", minWidth: 0 }}>{pageControls}</Box></Box>;
  return <Box sx={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr) auto", gridTemplateAreas: '"mode search controls"', gap: 1, alignItems: "center", p: 1, px: 1.5, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}><Box sx={{ gridArea: "mode" }}>{modeControl}</Box><Box sx={{ gridArea: "search", minWidth: 0 }}><PdfSearchField search={search} matches={matches} matchIndex={matchIndex} onSearch={onSearch} onSearchMatch={onSearchMatch} /></Box><Box sx={{ gridArea: "controls" }}>{pageControls}</Box></Box>;
}
function AnnotationOverlay({ items, activeSelection }: { items: Annotation[]; activeSelection?: number[][] }) { return <div className="annotation-overlay">{items.filter(isReadingNote).flatMap((item) => (item.position_json.rects ?? []).map((rect, index) => <span key={`${item.id}-${index}`} className="annotation-mark annotation-mark--highlight" style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${rect[2] * 100}%`, height: `${rect[3] * 100}%`, backgroundColor: item.color || DEFAULT_HIGHLIGHT_COLOR }} />))}{activeSelection?.map((rect, index) => <span key={`active-${index}`} className="annotation-mark annotation-mark--active" style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${rect[2] * 100}%`, height: `${rect[3] * 100}%` }} />)}</div>; }

function LazyPdfPage({ pageNumber, pageWidth, scrollRoot, items, activeSelection, search }: { pageNumber: number; pageWidth: number; scrollRoot: React.RefObject<HTMLDivElement | null>; items: Annotation[]; activeSelection?: number[][]; search: string }) {
  const pageRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(pageNumber <= 2);
  const [rendered, setRendered] = useState(false);
  useEffect(() => {
    if (visible || !pageRef.current) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { root: scrollRoot.current, rootMargin: "1400px 0px" });
    observer.observe(pageRef.current);
    return () => observer.disconnect();
  }, [scrollRoot, visible]);
  const textRenderer = useMemo(() => ({ str }: { str: string }) => highlightPdfText(str, search, items), [items, search]);
  return <div ref={pageRef} className="pdf-page" data-page-number={pageNumber} style={{ width: pageWidth, minHeight: rendered ? undefined : pageWidth * 1.414 }}>{visible && <><Page pageNumber={pageNumber} width={pageWidth} renderTextLayer renderAnnotationLayer customTextRenderer={textRenderer} onRenderSuccess={() => setRendered(true)} /><AnnotationOverlay items={items} activeSelection={activeSelection} /></>}</div>;
}

function SelectionHandles({ selection, mode, onChange }: { selection: SelectionState; mode: "text" | "pdf"; onChange: (selection: SelectionState) => void }) {
  const [, setLayoutVersion] = useState(0);
  useEffect(() => {
    let frame = 0;
    const reposition = () => { window.cancelAnimationFrame(frame); frame = window.requestAnimationFrame(() => setLayoutVersion((value) => value + 1)); };
    document.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => { document.removeEventListener("scroll", reposition, true); window.removeEventListener("resize", reposition); window.cancelAnimationFrame(frame); };
  }, []);
  const range = selectionDomRange(selection, mode);
  const rects = range ? [...range.getClientRects()].filter((rect) => rect.width > 0 && rect.height > 0) : [];
  if (!range || !rects.length) return null;
  const first = rects[0]; const last = rects[rects.length - 1];
  const scrollViewport = mode === "pdf"
    ? document.querySelector<HTMLElement>(".pdf-scroll")
    : document.querySelector<HTMLElement>(".mobile-reader-selection, .desktop-reader-selection");
  const viewportRect = scrollViewport?.getBoundingClientRect();
  const contentElement = mode === "pdf"
    ? document.querySelector<HTMLElement>(`.pdf-page[data-page-number="${selection.page}"]`)
    : document.querySelector<HTMLElement>(`[data-text-block][data-block-id="${CSS.escape(selection.position.block_id || "")}"]`)?.closest<HTMLElement>("article");
  const contentRect = contentElement?.getBoundingClientRect();
  const visibleBounds = viewportRect && contentRect ? {
    left: Math.max(viewportRect.left, contentRect.left),
    right: Math.min(viewportRect.right, contentRect.right),
    top: Math.max(viewportRect.top, contentRect.top),
    bottom: Math.min(viewportRect.bottom, contentRect.bottom),
  } : viewportRect || contentRect;
  function begin(edge: "start" | "end", anchorX: number, anchorY: number, event: React.PointerEvent<HTMLButtonElement>) {
    event.preventDefault(); event.stopPropagation();
    const handle = event.currentTarget;
    const pointerOffset = { x: event.clientX - anchorX, y: event.clientY - anchorY };
    handle.setPointerCapture(event.pointerId);
    handle.dataset.dragging = "true";
    const move = (next: PointerEvent) => {
      const handles = [...document.querySelectorAll<HTMLElement>("[data-selection-handle]")];
      handles.forEach((item) => { item.style.pointerEvents = "none"; });
      const point = caretAtPoint(next.clientX - pointerOffset.x, next.clientY - pointerOffset.y);
      handles.forEach((item) => { item.style.pointerEvents = ""; });
      if (!point) return;
      if (mode === "text") updateTextSelectionBoundary(selection, edge, point, onChange);
      else updatePdfSelectionBoundary(selection, edge, point, onChange);
    };
    const finish = () => { delete handle.dataset.dragging; window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", finish); window.removeEventListener("pointercancel", finish); };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", finish, { once: true });
    window.addEventListener("pointercancel", finish, { once: true });
  }
  const handles = [{ edge: "start" as const, x: first.left, y: first.bottom }, { edge: "end" as const, x: last.right, y: last.bottom }].filter(({ x, y }) => !visibleBounds || (x >= visibleBounds.left && x <= visibleBounds.right && y >= visibleBounds.top && y <= visibleBounds.bottom));
  return <>{handles.map(({ edge, x, y }) => {
    const start = edge === "start";
    return <Box component="button" type="button" data-selection-handle aria-label={start ? "调整选区起点" : "调整选区终点"} key={edge} onPointerDown={(event) => begin(edge, x, y, event)} sx={{ position: "fixed", zIndex: 28, left: start ? x - 36 : x - 8, top: y - 4, width: 44, height: 40, p: 0, border: 0, outline: 0, bgcolor: "transparent", touchAction: "none", cursor: "ew-resize", WebkitTapHighlightColor: "transparent", "& .selection-knob": { position: "absolute", ...(start ? { right: 8 } : { left: 8 }), top: 4, width: 18, height: 18, bgcolor: "primary.main", borderRadius: start ? "50% 0 50% 50%" : "0 50% 50% 50%", transformOrigin: start ? "100% 0" : "0 0", filter: "drop-shadow(0 2px 3px rgba(25,118,210,.24))", transition: "transform 100ms ease, filter 100ms ease" }, "&[data-dragging=true] .selection-knob, &:active .selection-knob": { transform: "scale(1.12)", filter: "drop-shadow(0 3px 5px rgba(25,118,210,.34))" } }}><Box component="span" className="selection-knob" aria-hidden /></Box>;
  })}</>;
}

function selectionDomRange(selection: SelectionState, mode: "text" | "pdf") {
  if (mode === "text" && selection.position.block_id) {
    const segments = selection.position.segments?.length
      ? selection.position.segments
      : [{ block_id: selection.position.block_id, start_offset: selection.position.start_offset || 0, end_offset: selection.position.end_offset || 0 }];
    const first = segments[0]; const last = segments.at(-1)!;
    const firstRoot = document.querySelector<HTMLElement>(`[data-text-block][data-block-id="${CSS.escape(first.block_id)}"]`);
    const lastRoot = document.querySelector<HTMLElement>(`[data-text-block][data-block-id="${CSS.escape(last.block_id)}"]`);
    const firstRange = firstRoot ? rangeFromOffsets(firstRoot, first.start_offset, first.end_offset) : null;
    const lastRange = lastRoot ? rangeFromOffsets(lastRoot, last.start_offset, last.end_offset) : null;
    if (!firstRange || !lastRange) return null;
    const combined = document.createRange();
    combined.setStart(firstRange.startContainer, firstRange.startOffset);
    combined.setEnd(lastRange.endContainer, lastRange.endOffset);
    return combined;
  }
  if (mode === "pdf" && selection.domRange?.kind === "pdf") {
    const root = document.querySelector<HTMLElement>(`.pdf-page[data-page-number="${selection.page}"] .react-pdf__Page__textContent`);
    if (!root) return null;
    return rangeFromOffsets(root, selection.domRange.start, selection.domRange.end);
  }
  return null;
}

function updateTextSelectionBoundary(selection: SelectionState, edge: "start" | "end", point: { node: Node; offset: number }, onChange: (selection: SelectionState) => void) {
  const pointElement = point.node.nodeType === Node.ELEMENT_NODE ? point.node as Element : point.node.parentElement;
  const pointBlock = pointElement?.closest<HTMLElement>("[data-text-block]");
  const article = pointBlock?.closest<HTMLElement>("article");
  const current = selectionDomRange(selection, "text");
  if (!pointBlock || !article || !current || !pointBlock.contains(point.node)) return;
  const range = document.createRange();
  try {
    if (edge === "start") {
      range.setStart(point.node, point.offset);
      range.setEnd(current.endContainer, current.endOffset);
    } else {
      range.setStart(current.startContainer, current.startOffset);
      range.setEnd(point.node, point.offset);
    }
  } catch {
    return;
  }
  if (range.collapsed) return;
  const next = textSelectionFromRange(article, range);
  if (!next) return;
  const first = next.segments[0];
  onChange({ ...selection, text: next.text, page: next.page, rect: next.rect, anchor: { x: next.rect.right, y: next.rect.bottom }, position: { block_id: first.blockId, start_offset: first.startOffset, end_offset: first.endOffset, segments: next.segments.map((segment) => ({ block_id: segment.blockId, start_offset: segment.startOffset, end_offset: segment.endOffset })) }, pageContext: next.pageContext });
}

function updatePdfSelectionBoundary(selection: SelectionState, edge: "start" | "end", point: { node: Node; offset: number }, onChange: (selection: SelectionState) => void) {
  if (selection.domRange?.kind !== "pdf") return;
  const page = document.querySelector<HTMLElement>(`.pdf-page[data-page-number="${selection.page}"]`);
  const root = page?.querySelector<HTMLElement>(".react-pdf__Page__textContent");
  if (!page || !root || !root.contains(point.node)) return;
  const pointOffset = offsetWithin(root, point.node, point.offset);
  if (pointOffset == null) return;
  const start = edge === "start" ? Math.min(pointOffset, selection.domRange.end - 1) : selection.domRange.start;
  const end = edge === "end" ? Math.max(pointOffset, selection.domRange.start + 1) : selection.domRange.end;
  const range = rangeFromOffsets(root, start, end);
  if (!range) return;
  const next = pdfSelectionFromRange(range, page, root);
  if (next) onChange({ ...selection, ...next, domRange: { kind: "pdf", start, end } });
}

function rangeOffsets(root: HTMLElement, range: Range) {
  const start = offsetWithin(root, range.startContainer, range.startOffset); const end = offsetWithin(root, range.endContainer, range.endOffset);
  return start == null || end == null ? null : { start: Math.min(start, end), end: Math.max(start, end) };
}

function pdfSelectionFromRange(range: Range, pageElement: HTMLElement, textLayer: HTMLElement, anchor?: { x: number; y: number }): Omit<SelectionState, "domRange"> | null {
  const text = range.toString(); const rect = range.getBoundingClientRect(); const pageRect = pageElement.getBoundingClientRect();
  const rects = [...range.getClientRects()].filter((item) => item.width > 1 && item.height > 1).map((item) => [(item.left - pageRect.left) / pageRect.width, (item.top - pageRect.top) / pageRect.height, item.width / pageRect.width, item.height / pageRect.height]);
  if (!text.trim() || !rects.length) return null;
  const offsets = rangeOffsets(textLayer, range); const pageText = textLayer.textContent || ""; const start = offsets?.start || 0; const end = offsets?.end || start + text.length;
  return { text, page: Number(pageElement.dataset.pageNumber || 1), rect, anchor: anchor ?? { x: rect.right, y: rect.bottom }, position: { rects }, pageContext: pageText.slice(Math.max(0, start - 300), end + 300) };
}

function escapeHtml(value: string) { return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] || character); }
function highlightPdfText(value: string, search: string, items: Annotation[]) {
  const marks: Array<{ start: number; end: number; search?: boolean; color?: string }> = [];
  const lowerValue = value.toLocaleLowerCase();
  const addToken = (token: string, mark: Omit<(typeof marks)[number], "start" | "end">) => {
    if (token.length < 2) return;
    const lowerToken = token.toLocaleLowerCase();
    let cursor = 0;
    while ((cursor = lowerValue.indexOf(lowerToken, cursor)) >= 0) {
      marks.push({ start: cursor, end: cursor + token.length, ...mark });
      cursor += token.length;
    }
  };
  addToken(search.trim(), { search: true });
  for (const item of items) {
    if (item.position_json.rects?.length || !item.selected_text) continue;
    addToken(item.selected_text.trim(), { color: /^#[0-9a-f]{6}$/i.test(item.color || "") ? item.color! : DEFAULT_HIGHLIGHT_COLOR });
  }
  if (!marks.length) return escapeHtml(value);
  const boundaries = [...new Set([0, value.length, ...marks.flatMap((mark) => [mark.start, mark.end])])].sort((a, b) => a - b);
  const parts: string[] = [];
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const start = boundaries[index]; const end = boundaries[index + 1];
    const active = marks.filter((mark) => mark.start <= start && mark.end >= end);
    const chunk = escapeHtml(value.slice(start, end));
    const annotation = active.find((mark) => mark.color);
    if (annotation) parts.push(`<mark style="background:${annotation.color}80;color:inherit;padding:0">${chunk}</mark>`);
    else if (active.some((mark) => mark.search)) parts.push(`<mark class="pdf-search-mark">${chunk}</mark>`);
    else parts.push(chunk);
  }
  return parts.join("");
}

function SelectionToolbar({ mobile, selection, paperId, note: savedNote, onClose, onSaveNote, onSaveTranslationNote, onEditNote, onAddToChat }: { mobile: boolean; selection: SelectionState; paperId: string; note?: Annotation; onClose: () => void; onSaveNote: (value: string, color: string) => void; onSaveTranslationNote: (value: string) => Promise<void>; onEditNote: () => void; onAddToChat: () => void }) {
  const [mode, setMode] = useState<"actions" | "note" | "translation">("actions"); const [note, setNote] = useState(""); const [color, setColor] = useState(DEFAULT_HIGHLIGHT_COLOR); const [translation, setTranslation] = useState(""); const [translating, setTranslating] = useState(false); const [savingTranslationNote, setSavingTranslationNote] = useState(false); const [connected, setConnected] = useState(false); const [cached, setCached] = useState(false); const [error, setError] = useState("");
  async function translate() { setMode("translation"); setTranslation(""); setConnected(false); setError(""); setTranslating(true); try { await streamApi(`/api/papers/${paperId}/translate/stream`, { text: selection.text, page_number: selection.page, target_language: "简体中文", position_json: selection.position, page_context: selection.pageContext }, (event) => { if (event.type === "meta") { setCached(Boolean(event.cached)); setConnected(true); } if (event.type === "delta") setTranslation((value) => value + String(event.delta ?? "")); }); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setTranslating(false); } }
  async function saveTranslationAsNote() { if (!translation.trim() || savingTranslationNote) return; setSavingTranslationNote(true); setError(""); try { await onSaveTranslationNote(translation); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); setSavingTranslationNote(false); } }
  if (mode === "actions") {
    return <Stack data-selection-toolbar direction="row" spacing={0.75} sx={{ position: "fixed", zIndex: 34, ...(mobile ? { left: "50%", bottom: "calc(12px + env(safe-area-inset-bottom))", transform: "translateX(-50%)" } : { left: Math.max(8, Math.min(selection.anchor.x + 10, window.innerWidth - 142)), top: Math.max(70, Math.min(selection.anchor.y + 10, window.innerHeight - 54)) }) }}><Tooltip title="翻译"><IconButton aria-label="翻译选中文字" onClick={translate} sx={floatingReaderControlSx}><LanguagesRounded /></IconButton></Tooltip><Tooltip title={savedNote ? "编辑高亮" : "高亮"}><IconButton aria-label={savedNote ? "编辑高亮" : "高亮选中文字"} onClick={savedNote ? onEditNote : () => setMode("note")} sx={floatingReaderControlSx}><HighlightRounded /></IconButton></Tooltip><Tooltip title="添加到聊天"><IconButton aria-label="添加选中文字到聊天" onClick={onAddToChat} sx={floatingReaderControlSx}><PsychologyRounded /></IconButton></Tooltip>{mobile && <Tooltip title="取消选择"><IconButton aria-label="取消选择" onClick={onClose} sx={floatingReaderControlSx}><CloseRounded /></IconButton></Tooltip>}</Stack>;
  }
  return <MuiPaper data-selection-toolbar elevation={8} sx={{ position: "fixed", zIndex: 40, ...(mobile ? { left: 8, right: 8, bottom: "calc(8px + env(safe-area-inset-bottom))", width: "auto", maxHeight: "calc(100dvh - 24px)" } : { left: Math.max(8, Math.min(selection.rect.left, window.innerWidth - 390)), top: Math.max(70, Math.min(selection.rect.bottom + 8, window.innerHeight - 340)), width: 380, maxWidth: "calc(100vw - 16px)" }), overflow: "auto" }}>{mode === "translation" ? <Box sx={{ p: 2 }}><Stack direction="row" alignItems="center" spacing={1}><LanguagesRounded color="primary" /><Typography fontWeight={700}>翻译</Typography>{cached && <Chip size="small" label="已缓存" color="success" />}<IconButton size="small" onClick={onClose} sx={{ ml: "auto!important" }}><CloseRounded /></IconButton></Stack><Box sx={{ mt: 1.5, minHeight: 38 }}>{translation ? <MarkdownMessage content={translation} /> : translating ? <Stack direction="row" spacing={1}><CircularProgress size={17} /><Typography variant="body2">{connected ? "翻译模型正在生成…" : "正在连接翻译服务…"}</Typography></Stack> : null}</Box>{error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}{translation && !translating && <Stack direction="row" justifyContent="flex-end" sx={{ mt: 1.5 }}><Button variant="outlined" startIcon={savingTranslationNote ? <CircularProgress size={15} /> : <NoteAddRounded />} disabled={savingTranslationNote} onClick={saveTranslationAsNote}>{savedNote ? "添加译文到笔记" : "保存为笔记"}</Button></Stack>}</Box> : <Box sx={{ p: 2 }}><Typography variant="subtitle2">高亮颜色</Typography><Stack direction="row" spacing={1} sx={{ my: 1.25 }}>{HIGHLIGHT_COLORS.map((value) => <Tooltip title={value} key={value}><IconButton aria-label={`高亮颜色 ${value}`} onClick={() => setColor(value)} sx={{ width: 32, height: 32, bgcolor: value, border: color === value ? 2 : 1, borderColor: color === value ? "primary.main" : "divider", "&:hover": { bgcolor: value } }}>{color === value && <CheckRounded fontSize="small" />}</IconButton></Tooltip>)}</Stack><TextField autoFocus fullWidth multiline minRows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="添加笔记，也可以留空" /><Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 1.5 }}><Button onClick={() => setMode("actions")}>返回</Button><Button variant="contained" onClick={() => onSaveNote(note, color)}>保存高亮</Button></Stack></Box>}</MuiPaper>;
}

function PaperConversation({ paperId, onJump, selections, onRemoveSelection, onClearSelections, architectureCards, onRemoveArchitectureCard, onClearArchitectureCards, composerRequest, externalBusy, architectureContext, onArchitectureCommands, onArchitectureSceneUpdated }: { paperId: string; onJump: (page: number) => void; selections: SelectionState[]; onRemoveSelection: (key: string) => void; onClearSelections: () => void; architectureCards: ArchitectureChatCard[]; onRemoveArchitectureCard: (id: string) => void; onClearArchitectureCards: () => void; composerRequest: ArchitectureComposerRequest | null; externalBusy: boolean; architectureContext: ArchitectureReaderContext | null; onArchitectureCommands: (commands: ArchitectureCommand[]) => void; onArchitectureSceneUpdated: () => void }) {
  const queryClient = useQueryClient(); const [liveUser, setLiveUser] = useState(""); const [liveRequestContext, setLiveRequestContext] = useState<Record<string, unknown>>({}); const [streaming, setStreaming] = useState(""); const [activities, setActivities] = useState<AgentActivityItem[]>([]); const [sending, setSending] = useState(false); const [error, setError] = useState(""); const [liveTaskId, setLiveTaskId] = useState<string | null>(null); const [liveAgentState, setLiveAgentState] = useState<AgentRuntimeState>({}); const [cancelling, setCancelling] = useState(false); const streamController = useRef<AbortController | null>(null); const cancellingRef = useRef(false); const composerRef = useRef<AgentComposerHandle>(null); const handledComposerRequestId = useRef<number | null>(null); const sendContentRef = useRef<((content: string, options?: ConversationSendOptions) => Promise<void>) | null>(null);
  const conversation = useQuery({ queryKey: ["paper-conversation", paperId], queryFn: async () => { const list = await api<Conversation[]>(`/api/conversations?kind=paper&reference_id=${paperId}`); return list[0] ? api<Conversation>(`/api/conversations/${list[0].id}`) : api<Conversation>("/api/conversations", jsonBody({ kind: "paper", reference_id: paperId, title: "论文问答" })); }, refetchInterval: (query) => hasActiveConversationTurn(query.state.data) ? 1200 : false });
  const persistedTask = activeConversationTask(conversation.data); const activeTaskId = liveTaskId ?? persistedTask?.id ?? null; const active = sending || Boolean(persistedTask);
  useEffect(() => { if (!composerRequest || handledComposerRequestId.current === composerRequest.id || !conversation.data || active || externalBusy) return; handledComposerRequestId.current = composerRequest.id; void sendContentRef.current?.(composerRequest.text, { cards: [composerRequest.card], excerpts: [], clearComposer: false, clearPendingReferences: false }); }, [composerRequest, conversation.data, active, externalBusy]);
  function handleStreamEvent(streamEvent: StreamEvent) { setLiveAgentState((value) => updateAgentStateFromStream(value, streamEvent)); if (streamEvent.type === "meta" && typeof streamEvent.task_id === "string") setLiveTaskId(streamEvent.task_id); if (streamEvent.type === "delta") setStreaming((value) => value + String(streamEvent.delta ?? "")); if (streamEvent.type === "activity") setActivities((value) => updateActivities(value, streamEvent)); if (streamEvent.type === "architecture_command" && Array.isArray(streamEvent.commands)) onArchitectureCommands(streamEvent.commands as ArchitectureCommand[]); if (streamEvent.type === "architecture_scene_updated") onArchitectureSceneUpdated(); }
  async function sendContent(content: string, options?: ConversationSendOptions) { if (!content || !conversation.data || active || externalBusy) return; const excerpts = options?.excerpts ?? selections.map((item) => ({ text: item.text, page_number: item.page, position: item.position })); const cards = options?.cards ?? architectureCards; const architecture = architectureContext || cards.length ? { mode: "architecture", ...(architectureContext ?? {}), ...(cards.length ? { cards } : {}) } : undefined; const contextJson = { ...(excerpts.length ? { selections: excerpts } : {}), ...(architecture ? { architecture } : {}) }; if (architectureContext) onArchitectureCommands([{ type: "clear-transient" }]); const controller = new AbortController(); streamController.current = controller; cancellingRef.current = false; if (options?.clearComposer !== false) composerRef.current?.clear(); setLiveUser(content); setLiveRequestContext(contextJson); if (options?.clearPendingReferences !== false) onClearArchitectureCards(); setStreaming(""); setActivities([]); setError(""); setLiveTaskId(null); setLiveAgentState({}); setSending(true); try { await streamApi(`/api/conversations/${conversation.data.id}/messages/stream`, { content, selected_text: excerpts.length === 1 ? excerpts[0].text : undefined, page_number: excerpts.length === 1 ? excerpts[0].page_number : undefined, context_json: contextJson }, handleStreamEvent, controller.signal); if (options?.clearPendingReferences !== false) onClearSelections(); } catch (cause) { if (!cancellingRef.current && !isRequestAbort(cause)) { setActivities((value) => failRunningActivities(value)); setError(cause instanceof Error ? cause.message : String(cause)); } } finally { streamController.current = null; setSending(false); await queryClient.invalidateQueries({ queryKey: ["paper-conversation", paperId] }); setLiveUser(""); setLiveRequestContext({}); setStreaming(""); setActivities([]); setLiveTaskId(null); setLiveAgentState({}); } }
  sendContentRef.current = sendContent;
  async function submit(event: FormEvent) { event.preventDefault(); const content = composerRef.current?.getValue().trim() ?? ""; await sendContent(content); }
  const stopAgent = useCallback(async () => { if (!activeTaskId || cancelling) return; cancellingRef.current = true; setCancelling(true); setError(""); try { await cancelTask(activeTaskId); streamController.current?.abort(); await queryClient.invalidateQueries({ queryKey: ["paper-conversation", paperId] }); } catch (cause) { cancellingRef.current = false; setError(cause instanceof Error ? cause.message : String(cause)); } finally { setCancelling(false); } }, [activeTaskId, cancelling, paperId, queryClient]);
  async function retryAssistant(assistantId: string) { if (!conversation.data || active) return; const controller = new AbortController(); streamController.current = controller; cancellingRef.current = false; setStreaming(""); setActivities([]); setError(""); setLiveTaskId(null); setLiveAgentState({}); setSending(true); try { await streamApi(`/api/conversations/${conversation.data.id}/messages/stream`, { content: "继续未完成的回答", retry_assistant_id: assistantId }, handleStreamEvent, controller.signal); } catch (cause) { if (!cancellingRef.current && !isRequestAbort(cause)) { setActivities((value) => failRunningActivities(value)); setError(cause instanceof Error ? cause.message : String(cause)); } } finally { streamController.current = null; setSending(false); await queryClient.invalidateQueries({ queryKey: ["paper-conversation", paperId] }); setStreaming(""); setActivities([]); setLiveTaskId(null); setLiveAgentState({}); } }
  const liveContext = liveRequestContext;
  return <Stack sx={{ height: "100%", minHeight: 0 }}><ConversationScroller contentVersion={`${conversation.data?.messages.length ?? 0}:${liveUser.length}:${streaming.length}:${activities.map((item) => item.status).join(",")}:${sending}`}>{!conversation.data?.messages.length && !liveUser && <MuiPaper variant="outlined" sx={{ p: 2 }}><Typography variant="h3">围绕论文继续提问</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: .75 }}>可以从核心问题、方法差异或实验证据开始。</Typography><Stack alignItems="flex-start" sx={{ mt: 1 }}>{["这篇论文解决的核心问题是什么？", "它相比同方向工作新增了什么？", "实验是否支持主要结论？"].map((value) => <Button size="small" key={value} onClick={() => composerRef.current?.setValue(value)}>{value}</Button>)}</Stack></MuiPaper>}{conversation.data?.messages.map((message) => <ConversationBubble message={message} onJump={onJump} retryDisabled={active} onRetry={message.role === "assistant" ? () => retryAssistant(message.id) : undefined} key={message.id} />)}{liveUser && <ConversationBubble message={{ id: "live-user", role: "user", content: liveUser, citations_json: [], context_json: liveContext, created_at: "" }} onJump={onJump} />}{(streaming || sending || activities.length > 0) && <AgentActivity items={activities} pending={sending && !cancelling}>{streaming ? renderCitations(streaming, onJump) : undefined}</AgentActivity>}{error && <Alert severity="error">{error}</Alert>}</ConversationScroller><Box component="form" onSubmit={submit} sx={{ p: 2, borderTop: 1, borderColor: "divider" }}>{selections.length > 0 && <Stack spacing={.75} sx={{ mb: 1 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="caption" color="text.secondary">已添加 {selections.length} 段原文</Typography>{selections.length > 1 && <Button size="small" color="inherit" onClick={onClearSelections}>全部移除</Button>}</Stack>{selections.map((item) => <MuiPaper variant="outlined" key={selectionKey(item)} sx={{ p: 1.25, position: "relative" }}><Chip size="small" label={`第 ${item.page} 页`} /><Typography variant="body2" sx={{ mt: .5, pr: 4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{item.text}</Typography><IconButton size="small" aria-label="移除原文" onClick={() => onRemoveSelection(selectionKey(item))} sx={{ position: "absolute", right: 4, top: 4 }}><CloseRounded /></IconButton></MuiPaper>)}</Stack>}<ArchitectureChatCards cards={architectureCards} removable onRemove={onRemoveArchitectureCard} onClear={onClearArchitectureCards} />{externalBusy && <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: .75 }}>同一论文 Agent 正在更新架构；可以先输入问题，任务完成后再发送。</Typography>}<AgentComposer ref={composerRef} onKeyDown={active || externalBusy ? undefined : submitChatOnEnter} placeholder={active ? "可以先输入修正后的问题，停止后重新发送" : "输入关于方法、实验或结论的问题"} active={active} cancelling={cancelling} stopDisabled={!activeTaskId || cancelling} sendDisabled={externalBusy} onStop={stopAgent} conversation={conversation.data} liveState={liveAgentState} /></Box></Stack>;
}
function hasActiveConversationTurn(conversation?: Conversation): boolean { return Boolean(activeConversationTask(conversation)); }

function architectureCardsFromContext(context: Record<string, unknown>): ArchitectureChatCard[] {
  const architecture = context.architecture;
  if (!architecture || typeof architecture !== "object") return [];
  const cards = (architecture as { cards?: unknown }).cards;
  if (!Array.isArray(cards)) return [];
  return cards.filter((card): card is ArchitectureChatCard => Boolean(card && typeof card === "object" && typeof (card as ArchitectureChatCard).id === "string" && typeof (card as ArchitectureChatCard).title === "string"));
}

function ArchitectureChatCards({ cards, removable = false, inverted = false, onRemove, onClear }: { cards: ArchitectureChatCard[]; removable?: boolean; inverted?: boolean; onRemove?: (id: string) => void; onClear?: () => void }) {
  if (!cards.length) return null;
  return <Stack spacing={.75} sx={{ mb: 1 }}>
    {removable && <Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="caption" color="text.secondary">已添加 {cards.length} 个架构节点</Typography>{cards.length > 1 && <Button size="small" color="inherit" onClick={onClear}>全部移除</Button>}</Stack>}
    {cards.map((card) => <Box key={card.id} sx={{ position: "relative", p: .9, pr: removable ? 4 : .9, borderRadius: 1, border: removable ? 1 : 0, borderColor: "divider", bgcolor: inverted ? "rgba(255,255,255,.14)" : "rgba(0,111,230,.055)" }}>
      <Stack direction="row" alignItems="baseline" flexWrap="wrap" columnGap={.75} rowGap={.2}><Typography variant="body2" sx={{ fontWeight: 700, overflowWrap: "anywhere" }}>{card.title}</Typography>{card.symbol && <ArchitectureMath value={card.symbol} sx={{ color: inverted ? "inherit" : "primary.dark", fontSize: ".88rem" }} />}</Stack>
      {card.architecture && <Typography variant="caption" display="block" sx={{ mt: .25, color: inverted ? "inherit" : "text.secondary", opacity: inverted ? .84 : 1 }}>{card.architecture}</Typography>}
      {removable && <IconButton size="small" aria-label={`移除架构节点 ${card.title}`} onClick={() => onRemove?.(card.id)} sx={{ position: "absolute", top: 4, right: 4 }}><CloseRounded fontSize="small" /></IconButton>}
    </Box>)}
  </Stack>;
}

function ConversationBubble({ message, onJump, retryDisabled = false, onRetry }: { message: Message; onJump: (page: number) => void; retryDisabled?: boolean; onRetry?: () => void }) {
  const excerpts = Array.isArray(message.context_json.selections) ? message.context_json.selections as Array<{ text?: string; page_number?: number }> : [];
  const architectureCards = architectureCardsFromContext(message.context_json);
  if (message.role === "assistant") {
    const streamStatus = typeof message.context_json.stream_status === "string" ? message.context_json.stream_status : undefined;
    const pending = streamStatus === "queued" || streamStatus === "running";
    const retryable = streamStatus === "failed" || streamStatus === "interrupted";
    return <Box><AgentActivity items={activitiesFromContext(message.context_json)} pending={pending} turnStatus={streamStatus}>{message.content ? renderCitations(message.content, onJump) : undefined}</AgentActivity>{retryable && onRetry && <Button size="small" startIcon={<ReplayRounded />} disabled={retryDisabled} onClick={onRetry} sx={{ mt: .75 }}>继续生成</Button>}</Box>;
  }
  return <MuiPaper elevation={0} sx={{ p: 1.5, alignSelf: "flex-end", maxWidth: "86%", bgcolor: "primary.main", border: 0, color: "primary.contrastText" }}><ArchitectureChatCards cards={architectureCards} inverted />{excerpts.length > 0 && <Stack spacing={.5} sx={{ mb: 1 }}>{excerpts.map((item, index) => <Box key={`${item.page_number}-${index}`} sx={{ p: .75, borderRadius: 1, bgcolor: "rgba(255,255,255,.14)" }}><Typography variant="caption">第 {item.page_number ?? "—"} 页</Typography><Typography variant="caption" display="block" sx={{ opacity: .84 }}>{item.text}</Typography></Box>)}</Stack>}{renderCitations(message.content, onJump, true)}</MuiPaper>;
}
function renderCitations(content: string, onJump: (page: number) => void, inverted = false) { return <MarkdownMessage content={content} onJump={onJump} inverted={inverted} />; }


function suggestedInterestLevel(paper: Paper): InterestLevel {
  const candidate = paper.pending_feedback?.suggested_interest_level;
  if (candidate === "ignored" || candidate === "low" || candidate === "neutral" || candidate === "high" || candidate === "interested") {
    return candidate;
  }
  return paper.pending_feedback?.suggested_interested ? "interested" : "neutral";
}

function suggestedQualityLevel(paper: Paper): QualityLevel {
  const candidate = paper.pending_feedback?.suggested_quality_level;
  if (candidate === "poor" || candidate === "below_average" || candidate === "normal" || candidate === "good" || candidate === "excellent") {
    return candidate;
  }
  return paper.pending_feedback?.suggested_quality ? "excellent" : "normal";
}

function FeedbackModal({ open, paper, processing, activities, processError, onClose, onSaved }: { open: boolean; paper: Paper; processing: boolean; activities: AgentActivityItem[]; processError: string; onClose: () => void; onSaved: () => void }) {
  const queryClient = useQueryClient();
  const [interestLevel, setInterestLevel] = useState<InterestLevel>(() => suggestedInterestLevel(paper));
  const [qualityLevel, setQualityLevel] = useState<QualityLevel>(() => suggestedQualityLevel(paper));
  const [selected, setSelected] = useState<string[]>([]);
  const [newTags, setNewTags] = useState<string[]>([]);
  const [selectedNodeLinks, setSelectedNodeLinks] = useState<ConfirmedNodeLink[]>([]);
  const [newNodes, setNewNodes] = useState<NewConfirmedNode[]>([]);
  const [summaryDraft, setSummaryDraft] = useState<CompletionSummaryDraft | null>(() => completionSummaryDraft(paper.completion_summary_structured));
  const wasOpen = useRef(false);
  const wasProcessing = useRef(false);
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api<Tag[]>("/api/tags"), enabled: open });
  const nodes = useQuery({ queryKey: ["graph-nodes", "feedback"], queryFn: () => api<ResearchNode[]>("/api/graph/nodes?limit=500"), enabled: open });
  const suggestedTags = (paper.pending_feedback?.suggested_tags as string[] | undefined) ?? [];
  const suggestedNodes = (Array.isArray(paper.pending_feedback?.suggested_nodes)
    ? paper.pending_feedback.suggested_nodes
    : []) as SuggestedNode[];
  const availableNodes = nodes.data ?? [];
  const confirm = useMutation({
    mutationFn: () => api(`/api/papers/${paper.id}/confirm-feedback`, jsonBody({
      interest_level: interestLevel,
      quality_level: qualityLevel,
      tag_ids: selected,
      new_tags: newTags,
      node_links: selectedNodeLinks,
      new_nodes: newNodes,
      completion_summary_structured: summaryDraft && completionSummaryDraftIsComplete(summaryDraft) ? summaryDraft : undefined,
    })),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["graph-nodes"] }),
        queryClient.invalidateQueries({ queryKey: ["subgraph"] }),
        queryClient.invalidateQueries({ queryKey: ["node"] }),
        queryClient.invalidateQueries({ queryKey: ["node-search"] }),
        queryClient.invalidateQueries({ queryKey: ["tags"] }),
        queryClient.invalidateQueries({ queryKey: ["papers"] }),
        queryClient.invalidateQueries({ queryKey: ["brief"] }),
      ]);
      onSaved();
    },
  });

  useEffect(() => {
    if (open && (!wasOpen.current || (wasProcessing.current && !processing))) {
      setInterestLevel(suggestedInterestLevel(paper));
      setQualityLevel(suggestedQualityLevel(paper));
      setSelected(paper.tags.map((tag) => tag.id));
      setNewTags([]);
      setSelectedNodeLinks(
        paper.nodes.map((node) => ({
          node_id: node.id,
          reason: node.reason ?? "",
          is_key: node.is_key ?? false,
        })),
      );
      setNewNodes([]);
      setSummaryDraft(completionSummaryDraft(paper.completion_summary_structured));
    }
    wasOpen.current = open;
    wasProcessing.current = processing;
  }, [open, paper, processing]);

  function acceptSuggestedTag(name: string) {
    const existing = (tags.data ?? paper.tags).find(
      (tag) => normalizeExactName(tag.name) === normalizeExactName(name),
    );
    if (existing) {
      setSelected((current) => current.includes(existing.id) ? current : [...current, existing.id]);
    } else if (!newTags.some((tag) => normalizeExactName(tag) === normalizeExactName(name))) {
      setNewTags((current) => [...current, name.trim()]);
    }
  }

  function acceptSuggestedNode(candidate: SuggestedNode) {
    const existing = candidate.node_id
      ? { id: candidate.node_id }
      : availableNodes.find(
        (node) => normalizeExactName(node.name) === normalizeExactName(candidate.name),
      );
    if (existing) {
      setSelectedNodeLinks((current) => current.some((item) => item.node_id === existing.id)
        ? current
        : [...current, { node_id: existing.id, reason: candidate.reason, is_key: false }]);
      return;
    }
    if (!newNodes.some((node) => normalizeExactName(node.name) === normalizeExactName(candidate.name))) {
      setNewNodes((current) => [...current, {
        name: candidate.name.trim(),
        core_question: candidate.core_question.trim(),
        domain: candidate.domain?.trim() || null,
        reason: candidate.reason.trim(),
        is_key: false,
      }]);
    }
  }

  const ready = !processing && !processError;
  const summaryComplete = !summaryDraft || completionSummaryDraftIsComplete(summaryDraft);
  const nodesComplete = selectedNodeLinks.every((link) => link.reason.trim())
    && newNodes.every(
      (node) => node.name.trim() && node.core_question.trim() && node.reason.trim(),
    );

  return (
    <Modal open={open} onClose={onClose} title={processing ? "正在整理精读结论" : processError ? "精读整理失败" : "确认精读结论"} wide fixedHeight>
      <Stack sx={{ height: "100%", minHeight: 0, overflow: "hidden" }}>
        <Box data-feedback-scroll sx={{ flex: "1 1 0", height: 0, minHeight: 0, overflowY: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable", scrollbarWidth: "thin", pr: { sm: .5 }, "&::-webkit-scrollbar": { width: 8 }, "&::-webkit-scrollbar-thumb": { bgcolor: "action.disabled", borderRadius: 999 }, "&::-webkit-scrollbar-track": { bgcolor: "transparent" } }}>
          <Stack spacing={2.5} sx={{ pb: 1 }}>
            <AgentActivity items={activities} pending={processing} autoCollapseWhenComplete={ready} />
            {processError && <Alert severity="error">{processError}</Alert>}
            {ready && <>
              {summaryDraft ? <CompletionSummaryEditor value={summaryDraft} onChange={setSummaryDraft} /> : <Alert severity="warning">本次整理没有生成结构化精读结论。</Alert>}
              {!summaryComplete && <Alert severity="warning">请补全所有精读总结后再保存。</Alert>}
              <Box>
                <Typography variant="h3" sx={{ mb: 1 }}>你的判断</Typography>
                <PaperEvaluationToggleButtons
                  researchScore={paper.research_score}
                  relevanceScore={paper.relevance_score}
                  qualityLevel={qualityLevel}
                  interestLevel={interestLevel}
                  onQualityChange={setQualityLevel}
                  onInterestChange={setInterestLevel}
                />
                <Typography variant="caption" color="text.secondary">质量与兴趣相互独立；“正常”和“相关”都不会作为负向评价。</Typography>
              </Box>
              <Box>
                <Typography variant="h3">标签</Typography>
                {suggestedTags.length > 0 && <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}><Typography variant="body2" color="text.secondary" sx={{ alignSelf: "center" }}>Agent 建议</Typography>{suggestedTags.map((name) => <Chip clickable color="primary" variant="outlined" label={name} key={name} onClick={() => acceptSuggestedTag(name)} />)}</Stack>}
                <PaperTagSelector tags={tags.data ?? paper.tags} selectedIds={selected} newTags={newTags} onSelectedIdsChange={setSelected} onNewTagsChange={setNewTags} />
              </Box>
              <Box>
                <Typography variant="h3">研究节点</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>保存后论文会立即挂载到这里确认的节点。</Typography>
                {suggestedNodes.length > 0 && <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}><Typography variant="body2" color="text.secondary" sx={{ alignSelf: "center" }}>Agent 建议</Typography>{suggestedNodes.map((node) => <Chip clickable color="primary" variant="outlined" label={node.name} key={`${node.node_id ?? "new"}:${node.name}`} onClick={() => acceptSuggestedNode(node)} />)}</Stack>}
                <PaperNodeSelector nodes={availableNodes} selectedLinks={selectedNodeLinks} newNodes={newNodes} onSelectedLinksChange={setSelectedNodeLinks} onNewNodesChange={setNewNodes} />
                {!nodesComplete && <Alert severity="warning" sx={{ mt: 1 }}>请补充核心研究问题和论文与节点的关系。</Alert>}
              </Box>
              {confirm.error && <Alert severity="error">{confirm.error.message}</Alert>}
            </>}
          </Stack>
        </Box>
        <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ flexShrink: 0, pt: 1.5, mt: 1.5, borderTop: 1, borderColor: "divider" }}>
          <Button onClick={onClose}>{processing ? "后台继续" : "稍后处理"}</Button>
          {ready && <Button variant="contained" onClick={() => confirm.mutate()} disabled={confirm.isPending || !summaryComplete || !nodesComplete}>保存结论</Button>}
        </Stack>
      </Stack>
    </Modal>
  );
}
