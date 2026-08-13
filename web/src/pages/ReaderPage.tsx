import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import CheckRounded from "@mui/icons-material/CheckRounded";
import ChevronLeftRounded from "@mui/icons-material/ChevronLeftRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import HighlightRounded from "@mui/icons-material/HighlightRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import KeyboardArrowUpRounded from "@mui/icons-material/KeyboardArrowUpRounded";
import LanguagesRounded from "@mui/icons-material/TranslateRounded";
import NoteAltRounded from "@mui/icons-material/NoteAltRounded";
import PendingActionsRounded from "@mui/icons-material/PendingActionsRounded";
import PaletteRounded from "@mui/icons-material/PaletteRounded";
import PsychologyRounded from "@mui/icons-material/PsychologyRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import SendRounded from "@mui/icons-material/SendRounded";
import TextFieldsRounded from "@mui/icons-material/TextFieldsRounded";
import ZoomInRounded from "@mui/icons-material/ZoomInRounded";
import ZoomOutRounded from "@mui/icons-material/ZoomOutRounded";
import { Alert, Box, Button, Chip, CircularProgress, Divider, FormControl, IconButton, InputAdornment, MenuItem, Paper as MuiPaper, Select, Stack, Tab, Tabs, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { useNavigate, useParams } from "react-router-dom";
import { api, formatDate, jsonBody, streamApi } from "../api";
import { AgentActivity } from "../components/AgentActivity";
import { activitiesFromContext, type AgentActivityItem, failRunningActivities, updateActivities } from "../components/agentActivityState";
import { submitChatOnEnter } from "../components/chatInput";
import { ConversationScroller } from "../components/ConversationScroller";
import { Modal } from "../components/Modal";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { MobileConfirmAction } from "../components/MobileConfirmAction";
import { PageHeader } from "../components/PageHeader";
import { PaperMetadataDialog } from "../components/PaperMetadataDialog";
import { PaperTagSelector } from "../components/PaperTagSelector";
import { caretAtPoint, offsetWithin, rangeFromOffsets, wordBounds } from "../components/readerSelection";
import { compactReaderControlSx, floatingReaderControlSx, readerControlSx, readerSearchFieldSx, readerSelectSx } from "../components/readerToolbarStyles";
import { ErrorState, Loading } from "../components/States";
import { TextPaperReader, type TextReaderSelection } from "../components/TextPaperReader";
import type { Annotation, Conversation, Message, Paper, PaperDocument, Tag } from "../types";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
type SelectionPosition = {
  rects?: number[][];
  block_id?: string;
  start_offset?: number;
  end_offset?: number;
  segments?: Array<{ block_id: string; start_offset: number; end_offset: number }>;
};
type SelectionState = { text: string; page: number; rect: DOMRect; anchor: { x: number; y: number }; position: SelectionPosition; pageContext: string; domRange?: { kind: "pdf"; start: number; end: number } };
const HIGHLIGHT_COLORS = ["#F3D76B", "#A8D8A8", "#9CCCF4", "#F3B1C8", "#D4B5F5"];
const DEFAULT_HIGHLIGHT_COLOR = "#F3D76B";
const isReadingNote = (item: Annotation) => item.kind === "note" || item.kind === "highlight";
const selectionKey = (item: SelectionState) => `${item.page}:${item.text}:${item.position.segments?.map((segment) => `${segment.block_id}:${segment.start_offset}:${segment.end_offset}`).join("|") || item.position.block_id || item.position.rects?.flat().join(":") || "text"}`;

export function ReaderPage() {
  const { paperId = "" } = useParams(); const navigate = useNavigate(); const queryClient = useQueryClient(); const theme = useTheme(); const mobile = useMediaQuery(theme.breakpoints.down("md"));
  const scrollRef = useRef<HTMLDivElement>(null); const saveTimer = useRef<number | undefined>(undefined); const mobileSelectionTimer = useRef<number | undefined>(undefined); const mobileSelectionStart = useRef<{ x: number; y: number } | null>(null);
  const [pageCount, setPageCount] = useState(0); const [currentPage, setCurrentPage] = useState(1); const [width, setWidth] = useState(760); const [zoom, setZoom] = useState(1); const [searchMatchIndex, setSearchMatchIndex] = useState(0);
  const [readerMode, setReaderMode] = useState<"text" | "pdf">(() => window.matchMedia("(max-width: 899px)").matches ? "text" : "pdf"); const [mobileView, setMobileView] = useState("reader"); const [readerToolsOpen, setReaderToolsOpen] = useState(false); const [sideTab, setSideTab] = useState("assistant"); const [sideWidth, setSideWidth] = useState(() => Number(localStorage.getItem("frontier-reader-side-width")) || 460);
  const [selection, setSelection] = useState<SelectionState | null>(null); const [chatSelections, setChatSelections] = useState<SelectionState[]>([]); const [editingNote, setEditingNote] = useState<Annotation | null>(null); const [search, setSearch] = useState(""); const [feedbackOpen, setFeedbackOpen] = useState(false); const [metadataOpen, setMetadataOpen] = useState(false); const [pdfError, setPdfError] = useState(""); const [finishActivities, setFinishActivities] = useState<AgentActivityItem[]>([]); const [finishing, setFinishing] = useState(false); const [finishError, setFinishError] = useState("");
  const paper = useQuery({ queryKey: ["paper", paperId], queryFn: () => api<Paper>(`/api/papers/${paperId}`) });
  const progress = useQuery({ queryKey: ["progress", paperId], queryFn: () => api<{ page_number: number; zoom: number }>(`/api/papers/${paperId}/progress`) });
  const annotations = useQuery({ queryKey: ["annotations", paperId], queryFn: () => api<Annotation[]>(`/api/papers/${paperId}/annotations`) });
  const textDocument = useQuery({ queryKey: ["paper-document", paperId], queryFn: () => api<PaperDocument>(`/api/papers/${paperId}/document`), enabled: readerMode === "text", refetchInterval: (query) => query.state.data?.status === "processing" ? 2000 : false });
  const rebuildDocument = useMutation({ mutationFn: () => api(`/api/papers/${paperId}/document/rebuild`, { method: "POST" }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["paper-document", paperId] }) });
  const pdfText = useQuery({ queryKey: ["pdf-text", paperId], queryFn: () => api<{ pages: Array<{ page_number: number; text: string }> }>(`/api/papers/${paperId}/text`), enabled: search.length > 1 });
  const createAnnotation = useMutation({ mutationFn: (payload: Record<string, unknown>) => api<Annotation>(`/api/papers/${paperId}/annotations`, jsonBody(payload)), onSuccess: () => { setSelection(null); window.getSelection()?.removeAllRanges(); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); } });
  const deleteAnnotation = useMutation({ mutationFn: (id: string) => api(`/api/papers/${paperId}/annotations/${id}`, { method: "DELETE" }), onSuccess: () => { setSelection(null); window.getSelection()?.removeAllRanges(); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); } });
  const saveProgress = useMutation({ mutationFn: (payload: { page_number: number; zoom: number }) => api(`/api/papers/${paperId}/progress`, { method: "PUT", body: JSON.stringify({ ...payload, scroll_offset: 0 }) }) });
  useEffect(() => { api(`/api/papers/${paperId}/start-reading`, { method: "POST" }).catch(() => undefined); }, [paperId]);
  useEffect(() => { if (progress.data) { const savedPage = progress.data.page_number; setCurrentPage(savedPage); setZoom(progress.data.zoom); if (pageCount > 0) window.setTimeout(() => scrollRef.current?.querySelector(`[data-page-number="${savedPage}"]`)?.scrollIntoView({ behavior: "auto", block: "start" }), 50); } }, [progress.data, pageCount]);
  useEffect(() => { const element = scrollRef.current; if (!element) return; const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.min(920, entry.contentRect.width - 32)))); observer.observe(element); return () => observer.disconnect(); }, [paper.data?.id, mobileView, readerMode]);
  useEffect(() => { if (paper.data?.status === "feedback_pending") setFeedbackOpen(true); }, [paper.data?.status]);
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
  function selectReaderMode(mode: "text" | "pdf") { setReaderMode(mode); setMobileView("reader"); setReaderToolsOpen(false); dismissSelection(); }
  function captureTextSelection(value: TextReaderSelection) { setSelection({ text: value.text, page: value.page, rect: value.rect, anchor: value.anchor, position: { block_id: value.blockId, start_offset: value.startOffset, end_offset: value.endOffset, segments: value.segments.map((segment) => ({ block_id: segment.blockId, start_offset: segment.startOffset, end_offset: segment.endOffset })) }, pageContext: value.pageContext }); }
  function addSelectionToChat() { if (!selection) return; setChatSelections((current) => current.some((item) => selectionKey(item) === selectionKey(selection)) ? current : [...current, selection]); setSideTab("assistant"); if (mobile) changeMobileView("assistant"); dismissSelection(); }
  useEffect(() => { if (!selection) return; const closeOutside = (event: PointerEvent) => { const target = event.target as Element | null; if (target?.closest("[data-selection-toolbar], [data-selection-handle]")) return; if (mobile) return; const nativeSelection = window.getSelection(); const inside = nativeSelection?.rangeCount ? [...nativeSelection.getRangeAt(0).getClientRects()].some((rect) => event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) : false; if (!inside) dismissSelection(); }; document.addEventListener("pointerdown", closeOutside); return () => document.removeEventListener("pointerdown", closeOutside); }, [mobile, selection]);
  function jumpTo(page: number, smooth = true) { if (mobile) { setReaderMode("pdf"); setMobileView("reader"); } window.setTimeout(() => { scrollRef.current?.querySelector(`[data-page-number="${page}"]`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }); }, 30); setCurrentPage(page); }
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
  return <Box sx={{ height: "100%", display: "flex", flexDirection: "column", bgcolor: "#EEEEEE", overflow: "hidden" }}>
    <PageHeader
      eyebrow={`论文阅读 · ${item.first_author || "作者信息待确认"} · ${formatDate(item.published_at)}`}
      title={item.title}
      actions={<><Tooltip title="返回上一页"><IconButton onClick={returnToPreviousPage} aria-label="返回上一页" sx={{ width: 36, height: 36 }}><ArrowBackRounded /></IconButton></Tooltip><Tooltip title="查看和编辑论文信息"><IconButton onClick={() => setMetadataOpen(true)} aria-label="论文信息" sx={{ width: 36, height: 36 }}><EditNoteRounded /></IconButton></Tooltip>{item.status === "feedback_pending" ? <><MobileConfirmAction label="待确认精读结论" icon={<PendingActionsRounded fontSize="small" />} color="warning" onConfirm={() => setFeedbackOpen(true)} /><Button size="small" variant="outlined" color="warning" onClick={() => setFeedbackOpen(true)} sx={{ display: { xs: "none", md: "inline-flex" } }}>待确认</Button></> : item.status !== "completed" && <><MobileConfirmAction label={finishing ? "正在整理精读结论" : "完成精读"} icon={finishing ? <CircularProgress size={17} /> : <CheckRounded fontSize="small" />} onConfirm={finishReading} disabled={finishing} /><Button size="small" variant="contained" startIcon={finishing ? <CircularProgress size={16} color="inherit" /> : <CheckRounded />} onClick={finishReading} disabled={finishing} sx={{ display: { xs: "none", md: "inline-flex" } }}>{finishing ? "正在整理" : "完成精读"}</Button></>}</>}
    />
    {mobile && <Stack direction="row" alignItems="center" sx={{ bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}><Tabs value={mobileView} onChange={(_, value) => changeMobileView(value)} variant="fullWidth" sx={{ minHeight: 40, flex: 1, "& .MuiTab-root": { minHeight: 40, py: 0, px: .5, minWidth: 0, fontSize: ".82rem" } }}><Tab value="reader" label={<Stack component="span" direction="row" alignItems="center" spacing={.15}>阅读<KeyboardArrowDownRounded sx={{ fontSize: 17, transition: "transform .18s ease", transform: readerToolsOpen ? "rotate(180deg)" : "rotate(0deg)" }} /></Stack>} aria-expanded={readerToolsOpen} onClick={() => { if (mobileView === "reader") setReaderToolsOpen((open) => !open); }} /><Tab value="assistant" label="问答" /><Tab value="notes" label={`笔记 ${noteItems.length}`} /></Tabs></Stack>}
    <Box sx={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden", "& > :last-child": { minHeight: 0, overflow: "hidden", ...(mobile && mobileView === "reader" ? { display: "none" } : {}) } }}>
      <Box sx={{ flex: 1, minWidth: 0, position: "relative", display: mobile && mobileView !== "reader" ? "none" : "flex", flexDirection: "column" }}>{readerMode === "text" ? <TextPaperReader document={textDocument.data} annotations={noteItems} mobile={mobile} toolsOpen={!mobile || readerToolsOpen} activeSelection={selection?.position.block_id ? { blockId: selection.position.block_id, startOffset: selection.position.start_offset || 0, endOffset: selection.position.end_offset || 0, segments: selection.position.segments?.map((segment) => ({ blockId: segment.block_id, startOffset: segment.start_offset, endOffset: segment.end_offset })) } : undefined} onRetry={() => rebuildDocument.mutate()} onModeToggle={() => selectReaderMode("pdf")} onSelection={captureTextSelection} /> : <>{(!mobile || readerToolsOpen) && <PdfToolbar mobile={mobile} currentPage={currentPage} pageCount={pageCount} zoom={zoom} search={search} matches={searchMatches.length} matchIndex={searchMatchIndex} onSearch={setSearch} onSearchMatch={jumpToSearchMatch} onJump={jumpTo} onZoom={setZoom} onModeToggle={() => selectReaderMode("text")} />}<Box className={mobile ? "pdf-scroll mobile-reader-selection" : "pdf-scroll desktop-reader-selection"} ref={scrollRef} onContextMenu={mobile ? (event) => event.preventDefault() : undefined} onPointerDown={beginMobilePdfSelection} onPointerMove={trackMobilePdfSelection} onPointerUp={cancelMobilePdfSelection} onPointerCancel={cancelMobilePdfSelection} onScroll={onScroll} onMouseUp={!mobile ? (event) => captureSelection({ x: event.clientX, y: event.clientY }) : undefined}><Document file={`/api/papers/${paperId}/pdf?v=${encodeURIComponent(item.pdf_version || "current")}`} onLoadSuccess={({ numPages }) => { setPageCount(numPages); setPdfError(""); }} onLoadError={(error) => setPdfError(error.message)} loading={<Loading label="正在加载 PDF" />} error={<ErrorState error={pdfError || "PDF 无法加载，请检查源文件。"} />}>{Array.from({ length: pageCount }, (_, index) => <LazyPdfPage key={index + 1} pageNumber={index + 1} pageWidth={width * zoom} scrollRoot={scrollRef} items={annotations.data?.filter((entry) => entry.page_number === index + 1) ?? []} activeSelection={mobile && selection?.page === index + 1 && selection.domRange?.kind === "pdf" ? selection.position.rects : undefined} search={search} />)}</Document></Box></>}{mobile && selection && <MobileSelectionHandles selection={selection} mode={readerMode} onChange={setSelection} />}{selection && <SelectionToolbar mobile={mobile} selection={selection} paperId={paperId} note={matchingNote} onClose={dismissSelection} onSaveNote={(content, color) => createAnnotation.mutate({ kind: "note", page_number: selection.page, selected_text: selection.text, content, position_json: selection.position, color })} onEditNote={() => { setEditingNote(matchingNote ?? null); dismissSelection(); }} onAddToChat={addSelectionToChat} />}</Box>
      {!mobile && <Box onPointerDown={startResize} sx={{ width: 7, cursor: "col-resize", bgcolor: "divider", "&:hover": { bgcolor: "primary.light" } }} />}
      <Box sx={{ width: mobile ? "100%" : sideWidth, minWidth: 0, display: mobile && mobileView === "pdf" ? "none" : "flex", flexDirection: "column", bgcolor: "background.paper" }}><Tabs value={mobile ? mobileView : sideTab} onChange={(_, value) => mobile ? setMobileView(value) : setSideTab(value)} sx={{ borderBottom: 1, borderColor: "divider", display: mobile ? "none" : "flex" }}><Tab value="assistant" label="论文问答" /><Tab value="notes" label={`笔记（${noteItems.length}）`} /></Tabs>{(mobile ? mobileView : sideTab) === "assistant" ? <PaperConversation paperId={paperId} onJump={jumpTo} selections={chatSelections} onRemoveSelection={(key) => setChatSelections((current) => current.filter((item) => selectionKey(item) !== key))} onClearSelections={() => setChatSelections([])} /> : <NotesPanel notes={noteItems} onJump={jumpTo} onEdit={setEditingNote} onDelete={(note) => deleteAnnotation.mutate(note.id)} />}</Box>
    </Box><NoteEditor note={editingNote} paperId={paperId} onClose={() => setEditingNote(null)} onDelete={() => editingNote && deleteAnnotation.mutate(editingNote.id, { onSuccess: () => setEditingNote(null) })} onSaved={() => { setEditingNote(null); queryClient.invalidateQueries({ queryKey: ["annotations", paperId] }); }} /><FeedbackModal open={feedbackOpen} paper={item} processing={finishing} activities={finishActivities} processError={finishError} onClose={() => setFeedbackOpen(false)} onJump={(page) => { setFeedbackOpen(false); jumpTo(page); }} onSaved={() => { setFeedbackOpen(false); queryClient.invalidateQueries({ queryKey: ["paper", paperId] }); }} /><PaperMetadataDialog paperId={paperId} open={metadataOpen} onClose={() => setMetadataOpen(false)} onJump={(page) => { setMetadataOpen(false); jumpTo(page); }} />
  </Box>;
}

type PdfToolbarProps = { mobile: boolean; currentPage: number; pageCount: number; zoom: number; search: string; matches: number; matchIndex: number; onSearch: (value: string) => void; onSearchMatch: (index: number) => void; onJump: (page: number) => void; onZoom: (value: number) => void; onModeToggle: () => void };
function PdfSearchField({ search, matches, matchIndex, onSearch, onSearchMatch, compact = false }: Pick<PdfToolbarProps, "search" | "matches" | "matchIndex" | "onSearch" | "onSearchMatch"> & { compact?: boolean }) { return <TextField fullWidth size="small" value={search} onChange={(event) => onSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); onSearchMatch(matchIndex + (event.shiftKey ? -1 : 1)); } }} placeholder="搜索全文" sx={readerSearchFieldSx} InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded sx={{ fontSize: compact ? 18 : 20 }} /></InputAdornment>, endAdornment: <InputAdornment position="end"><Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>{matches ? matchIndex + 1 : 0}/{matches}</Typography><IconButton disabled={!matches} aria-label="上一个匹配页" onClick={() => onSearchMatch(matchIndex - 1)} sx={compactReaderControlSx}><KeyboardArrowUpRounded fontSize="small" /></IconButton><IconButton disabled={!matches} aria-label="下一个匹配页" onClick={() => onSearchMatch(matchIndex + 1)} sx={compactReaderControlSx}><KeyboardArrowDownRounded fontSize="small" /></IconButton></InputAdornment> }} />; }
function PageSelect({ currentPage, pageCount, onJump }: Pick<PdfToolbarProps, "currentPage" | "pageCount" | "onJump">) { const total = Math.max(1, pageCount); const value = Math.min(Math.max(1, currentPage), total); return <FormControl size="small"><Select value={value} onChange={(event) => onJump(Number(event.target.value))} aria-label="PDF 页码" renderValue={(page) => `第 ${page} / ${pageCount || "—"} 页`} sx={{ ...readerSelectSx, minWidth: 112 }}>{Array.from({ length: total }, (_, index) => index + 1).map((page) => <MenuItem value={page} key={page}>第 {page} 页</MenuItem>)}</Select></FormControl>; }
function PdfToolbar({ mobile, currentPage, pageCount, zoom, search, matches, matchIndex, onSearch, onSearchMatch, onJump, onZoom, onModeToggle }: PdfToolbarProps) {
  const modeControl = <Tooltip title="切换到文本模式"><IconButton size="small" onClick={onModeToggle} sx={mobile ? compactReaderControlSx : readerControlSx}><TextFieldsRounded sx={{ fontSize: mobile ? 18 : 20 }} /></IconButton></Tooltip>;
  const pageControls = <Stack direction="row" justifyContent="flex-end" alignItems="center" spacing={mobile ? .5 : 1}><IconButton aria-label="上一页" onClick={() => onJump(Math.max(1, currentPage - 1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ChevronLeftRounded fontSize={mobile ? "small" : "medium"} /></IconButton><PageSelect currentPage={currentPage} pageCount={pageCount} onJump={onJump} /><IconButton aria-label="下一页" onClick={() => onJump(Math.min(pageCount || 1, currentPage + 1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ChevronRightRounded fontSize={mobile ? "small" : "medium"} /></IconButton><IconButton aria-label="缩小" onClick={() => onZoom(Math.max(.7, zoom - .1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ZoomOutRounded fontSize={mobile ? "small" : "medium"} /></IconButton><Typography variant="caption" sx={{ minWidth: mobile ? 34 : 38, textAlign: "center" }}>{Math.round(zoom * 100)}%</Typography><IconButton aria-label="放大" onClick={() => onZoom(Math.min(1.8, zoom + .1))} sx={mobile ? compactReaderControlSx : readerControlSx}><ZoomInRounded fontSize={mobile ? "small" : "medium"} /></IconButton></Stack>;
  if (mobile) return <Box sx={{ position: "absolute", zIndex: 6, top: 0, left: 0, right: 0, display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gridTemplateAreas: '"search search" "mode controls"', gap: 1, alignItems: "center", p: 1, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider", boxShadow: 2 }}><Box sx={{ gridArea: "search", minWidth: 0 }}><PdfSearchField compact search={search} matches={matches} matchIndex={matchIndex} onSearch={onSearch} onSearchMatch={onSearchMatch} /></Box><Box sx={{ gridArea: "mode" }}>{modeControl}</Box><Box sx={{ gridArea: "controls", minWidth: 0 }}>{pageControls}</Box></Box>;
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

function MobileSelectionHandles({ selection, mode, onChange }: { selection: SelectionState; mode: "text" | "pdf"; onChange: (selection: SelectionState) => void }) {
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
    : document.querySelector<HTMLElement>(".mobile-reader-selection");
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
    const root = document.querySelector<HTMLElement>(`[data-text-block][data-block-id="${CSS.escape(selection.position.block_id)}"]`);
    if (!root) return null;
    return rangeFromOffsets(root, selection.position.start_offset || 0, selection.position.end_offset || 0);
  }
  if (mode === "pdf" && selection.domRange?.kind === "pdf") {
    const root = document.querySelector<HTMLElement>(`.pdf-page[data-page-number="${selection.page}"] .react-pdf__Page__textContent`);
    if (!root) return null;
    return rangeFromOffsets(root, selection.domRange.start, selection.domRange.end);
  }
  return null;
}

function updateTextSelectionBoundary(selection: SelectionState, edge: "start" | "end", point: { node: Node; offset: number }, onChange: (selection: SelectionState) => void) {
  const blockId = selection.position.block_id;
  if (!blockId) return;
  const root = document.querySelector<HTMLElement>(`[data-text-block][data-block-id="${CSS.escape(blockId)}"]`);
  if (!root || !root.contains(point.node)) return;
  const pointOffset = offsetWithin(root, point.node, point.offset);
  if (pointOffset == null) return;
  const oldStart = selection.position.start_offset || 0; const oldEnd = selection.position.end_offset || oldStart + 1;
  const start = edge === "start" ? Math.min(pointOffset, oldEnd - 1) : oldStart;
  const end = edge === "end" ? Math.max(pointOffset, oldStart + 1) : oldEnd;
  const range = rangeFromOffsets(root, start, end);
  if (!range) return;
  const text = range.toString(); const rect = range.getBoundingClientRect();
  if (!text.trim() || !rect.width) return;
  const fullText = root.textContent || "";
  onChange({ ...selection, text, rect, anchor: { x: rect.right, y: rect.bottom }, position: { ...selection.position, start_offset: start, end_offset: end }, pageContext: fullText.slice(Math.max(0, start - 300), end + 300) });
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

function SelectionToolbar({ mobile, selection, paperId, note: savedNote, onClose, onSaveNote, onEditNote, onAddToChat }: { mobile: boolean; selection: SelectionState; paperId: string; note?: Annotation; onClose: () => void; onSaveNote: (value: string, color: string) => void; onEditNote: () => void; onAddToChat: () => void }) {
  const [mode, setMode] = useState<"actions" | "note" | "translation">("actions"); const [note, setNote] = useState(""); const [color, setColor] = useState(DEFAULT_HIGHLIGHT_COLOR); const [translation, setTranslation] = useState(""); const [translating, setTranslating] = useState(false); const [connected, setConnected] = useState(false); const [cached, setCached] = useState(false); const [error, setError] = useState("");
  async function translate() { setMode("translation"); setTranslation(""); setConnected(false); setError(""); setTranslating(true); try { await streamApi(`/api/papers/${paperId}/translate/stream`, { text: selection.text, page_number: selection.page, target_language: "简体中文", position_json: selection.position, page_context: selection.pageContext }, (event) => { if (event.type === "meta") { setCached(Boolean(event.cached)); setConnected(true); } if (event.type === "delta") setTranslation((value) => value + String(event.delta ?? "")); }); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setTranslating(false); } }
  if (mode === "actions") {
    return <Stack data-selection-toolbar direction="row" spacing={0.75} sx={{ position: "fixed", zIndex: 34, ...(mobile ? { left: "50%", bottom: "calc(12px + env(safe-area-inset-bottom))", transform: "translateX(-50%)" } : { left: Math.max(8, Math.min(selection.anchor.x + 10, window.innerWidth - 142)), top: Math.max(70, Math.min(selection.anchor.y + 10, window.innerHeight - 54)) }) }}><Tooltip title="翻译"><IconButton aria-label="翻译选中文字" onClick={translate} sx={floatingReaderControlSx}><LanguagesRounded /></IconButton></Tooltip><Tooltip title={savedNote ? "编辑高亮" : "高亮"}><IconButton aria-label={savedNote ? "编辑高亮" : "高亮选中文字"} onClick={savedNote ? onEditNote : () => setMode("note")} sx={floatingReaderControlSx}><HighlightRounded /></IconButton></Tooltip><Tooltip title="添加到聊天"><IconButton aria-label="添加选中文字到聊天" onClick={onAddToChat} sx={floatingReaderControlSx}><PsychologyRounded /></IconButton></Tooltip>{mobile && <Tooltip title="取消选择"><IconButton aria-label="取消选择" onClick={onClose} sx={floatingReaderControlSx}><CloseRounded /></IconButton></Tooltip>}</Stack>;
  }
  return <MuiPaper data-selection-toolbar elevation={8} sx={{ position: "fixed", zIndex: 40, ...(mobile ? { left: 8, right: 8, bottom: "calc(8px + env(safe-area-inset-bottom))", width: "auto", maxHeight: "calc(100dvh - 24px)" } : { left: Math.max(8, Math.min(selection.rect.left, window.innerWidth - 390)), top: Math.max(70, Math.min(selection.rect.bottom + 8, window.innerHeight - 310)), width: 380, maxWidth: "calc(100vw - 16px)" }), overflow: "auto" }}>{mode === "translation" ? <Box sx={{ p: 2 }}><Stack direction="row" alignItems="center" spacing={1}><LanguagesRounded color="primary" /><Typography fontWeight={700}>翻译</Typography>{cached && <Chip size="small" label="已缓存" color="success" />}<IconButton size="small" onClick={onClose} sx={{ ml: "auto!important" }}><CloseRounded /></IconButton></Stack><Box sx={{ mt: 1.5, minHeight: 38 }}>{translation ? <MarkdownMessage content={translation} /> : translating ? <Stack direction="row" spacing={1}><CircularProgress size={17} /><Typography variant="body2">{connected ? "翻译模型正在生成…" : "正在连接翻译服务…"}</Typography></Stack> : null}</Box>{error && <Alert severity="error">{error}</Alert>}</Box> : <Box sx={{ p: 2 }}><Typography variant="subtitle2">高亮颜色</Typography><Stack direction="row" spacing={1} sx={{ my: 1.25 }}>{HIGHLIGHT_COLORS.map((value) => <Tooltip title={value} key={value}><IconButton aria-label={`高亮颜色 ${value}`} onClick={() => setColor(value)} sx={{ width: 32, height: 32, bgcolor: value, border: color === value ? 2 : 1, borderColor: color === value ? "primary.main" : "divider", "&:hover": { bgcolor: value } }}>{color === value && <CheckRounded fontSize="small" />}</IconButton></Tooltip>)}</Stack><TextField autoFocus fullWidth multiline minRows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="添加笔记，也可以留空" /><Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 1.5 }}><Button onClick={() => setMode("actions")}>返回</Button><Button variant="contained" onClick={() => onSaveNote(note, color)}>保存高亮</Button></Stack></Box>}</MuiPaper>;
}

function PaperConversation({ paperId, onJump, selections, onRemoveSelection, onClearSelections }: { paperId: string; onJump: (page: number) => void; selections: SelectionState[]; onRemoveSelection: (key: string) => void; onClearSelections: () => void }) {
  const queryClient = useQueryClient(); const [text, setText] = useState(""); const [liveUser, setLiveUser] = useState(""); const [streaming, setStreaming] = useState(""); const [activities, setActivities] = useState<AgentActivityItem[]>([]); const [sending, setSending] = useState(false); const [error, setError] = useState("");
  const conversation = useQuery({ queryKey: ["paper-conversation", paperId], queryFn: async () => { const list = await api<Conversation[]>(`/api/conversations?kind=paper&reference_id=${paperId}`); return list[0] ? api<Conversation>(`/api/conversations/${list[0].id}`) : api<Conversation>("/api/conversations", jsonBody({ kind: "paper", reference_id: paperId, title: "论文问答" })); }, refetchInterval: (query) => hasActiveConversationTurn(query.state.data) ? 1200 : false });
  async function submit(event: FormEvent) { event.preventDefault(); const content = text.trim(); if (!content || !conversation.data || sending) return; const excerpts = selections.map((item) => ({ text: item.text, page_number: item.page, position: item.position })); setText(""); setLiveUser(content); setStreaming(""); setActivities([]); setError(""); setSending(true); try { await streamApi(`/api/conversations/${conversation.data.id}/messages/stream`, { content, selected_text: excerpts.length === 1 ? excerpts[0].text : undefined, page_number: excerpts.length === 1 ? excerpts[0].page_number : undefined, context_json: excerpts.length ? { selections: excerpts } : {} }, (streamEvent) => { if (streamEvent.type === "delta") setStreaming((value) => value + String(streamEvent.delta ?? "")); if (streamEvent.type === "activity") setActivities((value) => updateActivities(value, streamEvent)); }); onClearSelections(); } catch (cause) { setActivities((value) => failRunningActivities(value)); setError(cause instanceof Error ? cause.message : String(cause)); } finally { setSending(false); await queryClient.invalidateQueries({ queryKey: ["paper-conversation", paperId] }); setLiveUser(""); setStreaming(""); setActivities([]); } }
  return <Stack sx={{ height: "100%", minHeight: 0 }}><ConversationScroller contentVersion={`${conversation.data?.messages.length ?? 0}:${liveUser.length}:${streaming.length}:${activities.map((item) => item.status).join(",")}:${sending}`}>{!conversation.data?.messages.length && !liveUser && <MuiPaper variant="outlined" sx={{ p: 2 }}><Typography variant="h3">围绕论文继续提问</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: .75 }}>可以从核心问题、方法差异或实验证据开始。</Typography><Stack alignItems="flex-start" sx={{ mt: 1 }}>{["这篇论文解决的核心问题是什么？", "它相比同方向工作新增了什么？", "实验是否支持主要结论？"].map((value) => <Button size="small" key={value} onClick={() => setText(value)}>{value}</Button>)}</Stack></MuiPaper>}{conversation.data?.messages.map((message) => <ConversationBubble message={message} onJump={onJump} key={message.id} />)}{liveUser && <ConversationBubble message={{ id: "live-user", role: "user", content: liveUser, citations_json: [], context_json: selections.length ? { selections: selections.map((item) => ({ text: item.text, page_number: item.page })) } : {}, created_at: "" }} onJump={onJump} />}{(streaming || sending || activities.length > 0) && <AgentActivity items={activities} pending={sending}>{streaming ? renderCitations(streaming, onJump) : undefined}</AgentActivity>}{error && <Alert severity="error">{error}</Alert>}</ConversationScroller><Box component="form" onSubmit={submit} sx={{ p: 2, borderTop: 1, borderColor: "divider" }}>{selections.length > 0 && <Stack spacing={.75} sx={{ mb: 1 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="caption" color="text.secondary">已添加 {selections.length} 段原文</Typography>{selections.length > 1 && <Button size="small" color="inherit" onClick={onClearSelections}>全部移除</Button>}</Stack>{selections.map((item) => <MuiPaper variant="outlined" key={selectionKey(item)} sx={{ p: 1.25, position: "relative" }}><Chip size="small" label={`第 ${item.page} 页`} /><Typography variant="body2" sx={{ mt: .5, pr: 4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{item.text}</Typography><IconButton size="small" aria-label="移除原文" onClick={() => onRemoveSelection(selectionKey(item))} sx={{ position: "absolute", right: 4, top: 4 }}><CloseRounded /></IconButton></MuiPaper>)}</Stack>}<TextField fullWidth multiline minRows={2} maxRows={6} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={submitChatOnEnter} placeholder="输入关于方法、实验或结论的问题" disabled={sending} InputProps={{ endAdornment: <IconButton type="submit" color="primary" disabled={!text.trim() || sending}><SendRounded /></IconButton> }} /></Box></Stack>;
}
function hasActiveConversationTurn(conversation?: Conversation): boolean { const status = conversation?.messages.at(-1)?.context_json.stream_status; return status === "queued" || status === "running"; }
function ConversationBubble({ message, onJump }: { message: Message; onJump: (page: number) => void }) { const excerpts = Array.isArray(message.context_json.selections) ? message.context_json.selections as Array<{ text?: string; page_number?: number }> : []; if (message.role === "assistant") { const streamStatus = typeof message.context_json.stream_status === "string" ? message.context_json.stream_status : undefined; const pending = streamStatus === "queued" || streamStatus === "running"; return <AgentActivity items={activitiesFromContext(message.context_json)} pending={pending} turnStatus={streamStatus}>{message.content ? renderCitations(message.content, onJump) : undefined}</AgentActivity>; } return <MuiPaper elevation={0} sx={{ p: 1.5, alignSelf: "flex-end", maxWidth: "86%", bgcolor: "primary.main", border: 0, color: "primary.contrastText" }}>{excerpts.length > 0 && <Stack spacing={.5} sx={{ mb: 1 }}>{excerpts.map((item, index) => <Box key={`${item.page_number}-${index}`} sx={{ p: .75, borderRadius: 1, bgcolor: "rgba(255,255,255,.14)" }}><Typography variant="caption">第 {item.page_number ?? "—"} 页</Typography><Typography variant="caption" display="block" sx={{ opacity: .84 }}>{item.text}</Typography></Box>)}</Stack>}{renderCitations(message.content, onJump, true)}</MuiPaper>; }
function renderCitations(content: string, onJump: (page: number) => void, inverted = false) { return <MarkdownMessage content={content} onJump={onJump} inverted={inverted} />; }

function NotesPanel({ notes, onJump, onEdit, onDelete }: { notes: Annotation[]; onJump: (page: number) => void; onEdit: (note: Annotation) => void; onDelete: (note: Annotation) => void }) { return <Box sx={{ p: 2, overflowY: "auto" }}>{!notes.length ? <Stack alignItems="center" spacing={1} sx={{ py: 8, color: "text.secondary" }}><NoteAltRounded /><Typography>还没有笔记</Typography><Typography variant="body2">选中原文后可以高亮或记录想法。</Typography></Stack> : <Stack spacing={1.5}>{notes.map((note) => <MuiPaper variant="outlined" key={note.id} sx={{ p: 1.75, cursor: "pointer", borderLeft: `4px solid ${note.color || DEFAULT_HIGHLIGHT_COLOR}` }} onClick={() => onJump(note.page_number)}><Stack direction="row" justifyContent="space-between" alignItems="center"><Chip size="small" label={`第 ${note.page_number} 页`} /><Stack direction="row"><Tooltip title="编辑笔记和高亮颜色"><IconButton size="small" onClick={(event) => { event.stopPropagation(); onEdit(note); }}><EditRounded /></IconButton></Tooltip><Tooltip title="删除笔记和高亮"><IconButton size="small" color="error" onClick={(event) => { event.stopPropagation(); onDelete(note); }}><DeleteOutlineRounded /></IconButton></Tooltip></Stack></Stack>{note.selected_text && <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>{note.selected_text}</Typography>}<Divider sx={{ my: 1 }} /><Typography color={note.content ? "text.primary" : "text.secondary"} sx={{ whiteSpace: "pre-wrap", fontStyle: note.content ? "normal" : "italic" }}>{note.content || "未添加笔记"}</Typography></MuiPaper>)}</Stack>}</Box>; }
function NoteEditor({ note, paperId, onClose, onDelete, onSaved }: { note: Annotation | null; paperId: string; onClose: () => void; onDelete: () => void; onSaved: () => void }) { const [content, setContent] = useState(""); const [color, setColor] = useState(DEFAULT_HIGHLIGHT_COLOR); useEffect(() => { setContent(note?.content || ""); setColor(note?.color || DEFAULT_HIGHLIGHT_COLOR); }, [note]); const save = useMutation({ mutationFn: () => api(`/api/papers/${paperId}/annotations/${note!.id}`, { method: "PATCH", body: JSON.stringify({ kind: "note", page_number: note!.page_number, selected_text: note!.selected_text, content, position_json: note!.position_json, color }) }), onSuccess: onSaved }); return <Modal open={!!note} onClose={onClose} title="编辑笔记"><Stack spacing={2}><Box><Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}><PaletteRounded fontSize="small" color="action" /><Typography variant="subtitle2">高亮颜色</Typography></Stack><Stack direction="row" spacing={1}>{HIGHLIGHT_COLORS.map((value) => <Tooltip title={value} key={value}><IconButton aria-label={`高亮颜色 ${value}`} onClick={() => setColor(value)} sx={{ width: 34, height: 34, bgcolor: value, border: color === value ? 2 : 1, borderColor: color === value ? "primary.main" : "divider", "&:hover": { bgcolor: value } }}>{color === value && <CheckRounded fontSize="small" />}</IconButton></Tooltip>)}</Stack></Box><TextField fullWidth multiline minRows={6} value={content} onChange={(event) => setContent(event.target.value)} placeholder="记录你的想法，也可以保持空白" /><Stack direction="row" justifyContent="space-between" spacing={1}><Button color="error" startIcon={<DeleteOutlineRounded />} onClick={onDelete}>删除笔记和高亮</Button><Stack direction="row" spacing={1}><Button onClick={onClose}>取消</Button><Button variant="contained" onClick={() => save.mutate()} disabled={save.isPending}>保存</Button></Stack></Stack></Stack></Modal>; }

function FeedbackModal({ open, paper, processing, activities, processError, onClose, onJump, onSaved }: { open: boolean; paper: Paper; processing: boolean; activities: AgentActivityItem[]; processError: string; onClose: () => void; onJump: (page: number) => void; onSaved: () => void }) {
  const [interested, setInterested] = useState(Boolean(paper.pending_feedback?.suggested_interested)); const [quality, setQuality] = useState(Boolean(paper.pending_feedback?.suggested_quality)); const [createNode, setCreateNode] = useState(Boolean(paper.pending_feedback?.suggested_create_node)); const [selected, setSelected] = useState<string[]>([]); const [newTags, setNewTags] = useState<string[]>([]); const wasOpen = useRef(false); const wasProcessing = useRef(false);
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api<Tag[]>("/api/tags"), enabled: open });
  const confirm = useMutation({ mutationFn: () => api(`/api/papers/${paper.id}/confirm-feedback`, jsonBody({ interested, quality, create_node: createNode, tag_ids: selected, new_tags: newTags })), onSuccess: onSaved }); const suggested = (paper.pending_feedback?.suggested_tags as string[] | undefined) ?? [];
  useEffect(() => { if (open && (!wasOpen.current || (wasProcessing.current && !processing))) { setInterested(Boolean(paper.pending_feedback?.suggested_interested)); setQuality(Boolean(paper.pending_feedback?.suggested_quality)); setCreateNode(Boolean(paper.pending_feedback?.suggested_create_node)); setSelected(paper.tags.map((tag) => tag.id)); setNewTags([]); } wasOpen.current = open; wasProcessing.current = processing; }, [open, paper, processing]);
  function acceptSuggestedTag(name: string) { const existing = (tags.data ?? paper.tags).find((tag) => tag.name.toLowerCase() === name.toLowerCase()); if (existing) setSelected((current) => current.includes(existing.id) ? current : [...current, existing.id]); else if (!newTags.some((tag) => tag.toLowerCase() === name.toLowerCase())) setNewTags((current) => [...current, name]); }
  const ready = !processing && !processError;
  return <Modal open={open} onClose={onClose} title={processing ? "正在整理精读结论" : processError ? "精读整理失败" : "确认精读结论"} wide fixedHeight><Stack spacing={2.5} sx={{ height: "100%", minHeight: 0, overflowY: "auto", pr: { sm: .5 } }}><AgentActivity items={activities} pending={processing && activities.length === 0} autoCollapseWhenComplete={ready} />{processError && <Alert severity="error">{processError}</Alert>}{ready && <><Alert severity="info" sx={{ alignItems: "flex-start", "& .MuiAlert-message": { width: "100%" } }}><MarkdownMessage content={paper.completion_summary || "精读材料已经整理完成，请确认最终判断。"} onJump={onJump} /></Alert><Stack direction={{ xs: "column", sm: "row" }} gap={1}>{[["我感兴趣", interested, setInterested], ["优质论文", quality, setQuality], ["创建新节点", createNode, setCreateNode]].map(([label, active, setter]) => <Chip clickable color={active ? "primary" : "default"} variant={active ? "filled" : "outlined"} icon={active ? <CheckRounded /> : undefined} label={String(label)} onClick={() => (setter as (value: boolean) => void)(!active)} key={String(label)} />)}</Stack><Box><Typography variant="h3">标签</Typography>{suggested.length > 0 && <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}><Typography variant="body2" color="text.secondary" sx={{ alignSelf: "center" }}>Agent 建议</Typography>{suggested.map((name) => <Chip clickable color="primary" variant="outlined" label={name} key={name} onClick={() => acceptSuggestedTag(name)} />)}</Stack>}<PaperTagSelector tags={tags.data ?? paper.tags} selectedIds={selected} newTags={newTags} onSelectedIdsChange={setSelected} onNewTagsChange={setNewTags} /></Box>{confirm.error && <Alert severity="error">{confirm.error.message}</Alert>}</>}<Stack direction="row" justifyContent="flex-end" spacing={1}><Button onClick={onClose}>{processing ? "后台继续" : "稍后处理"}</Button>{ready && <Button variant="contained" onClick={() => confirm.mutate()} disabled={confirm.isPending}>保存结论</Button>}</Stack></Stack></Modal>;
}
