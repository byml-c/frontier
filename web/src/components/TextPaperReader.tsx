import CloseRounded from "@mui/icons-material/CloseRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import PictureAsPdfRounded from "@mui/icons-material/PictureAsPdfRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import KeyboardArrowUpRounded from "@mui/icons-material/KeyboardArrowUpRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import DOMPurify from "dompurify";
import katex from "katex";
import "katex/contrib/copy-tex";
import "katex/dist/katex.min.css";
import { Box, Button, Dialog, DialogContent, DialogTitle, FormControl, IconButton, InputAdornment, LinearProgress, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material";
import { useEffect, useMemo, useRef, useState } from "react";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import type { Annotation, DocumentBlock, PaperDocument } from "../types";
import { caretAtPoint, offsetWithin, rangeFromOffsets, textSelectionFromRange, wordBounds } from "./readerSelection";
import { compactReaderControlSx, readerControlSx, readerSearchFieldSx, readerSelectSx } from "./readerToolbarStyles";
import { ErrorState } from "./States";

export type TextReaderSelection = {
  text: string;
  page: number;
  blockId: string;
  startOffset: number;
  endOffset: number;
  segments: Array<{ blockId: string; startOffset: number; endOffset: number }>;
  rect: DOMRect;
  anchor: { x: number; y: number };
  pageContext: string;
};

type TextReaderActiveSelection = {
  blockId?: string;
  startOffset?: number;
  endOffset?: number;
  segments?: Array<{ blockId: string; startOffset: number; endOffset: number }>;
};

type HighlightRegistry = {
  set: (name: string, highlight: unknown) => void;
  delete: (name: string) => boolean;
};

type HighlightWindow = Window & typeof globalThis & {
  Highlight?: new (...ranges: Range[]) => unknown;
};

function customHighlightApi() {
  if (typeof CSS === "undefined" || typeof window === "undefined") return null;
  const registry = (CSS as typeof CSS & { highlights?: HighlightRegistry }).highlights;
  const HighlightConstructor = (window as HighlightWindow).Highlight;
  return registry && HighlightConstructor ? { registry, HighlightConstructor } : null;
}

type Props = {
  document: PaperDocument | undefined;
  annotations: Annotation[];
  mobile: boolean;
  toolsOpen: boolean;
  activeSelection?: TextReaderActiveSelection;
  onRetry: () => void;
  onModeToggle: () => void;
  onSelection: (selection: TextReaderSelection) => void;
};

export function TextPaperReader({ document, annotations, mobile, toolsOpen, activeSelection, onRetry, onModeToggle, onSelection }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const longPressTimer = useRef<number | undefined>(undefined);
  const longPressStart = useRef<{ x: number; y: number } | null>(null);
  const [search, setSearch] = useState("");
  const [matchIndex, setMatchIndex] = useState(0);
  const [fontSize, setFontSize] = useState(() => Math.min(18, Math.max(14, Number(localStorage.getItem("frontier-text-font-size")) || 17)));
  const [lineHeight, setLineHeight] = useState(() => Number(localStorage.getItem("frontier-text-line-height")) || 1.75);
  const [asset, setAsset] = useState<{ url?: string; html?: string; title: string } | null>(null);
  const blocks = useMemo(() => document?.blocks ?? [], [document?.blocks]);
  const matches = useMemo(() => collectMatches(blocks, search), [blocks, search]);

  useEffect(() => setMatchIndex(0), [search]);
  useEffect(() => {
    const api = customHighlightApi();
    if (!api) return;
    const name = "frontier-active-selection";
    api.registry.delete(name);
    if (!activeSelection) return;
    const segments = activeSelection.segments?.length
      ? activeSelection.segments
      : activeSelection.blockId && activeSelection.startOffset != null && activeSelection.endOffset != null
        ? [{ blockId: activeSelection.blockId, startOffset: activeSelection.startOffset, endOffset: activeSelection.endOffset }]
        : [];
    const ranges = segments.flatMap((segment) => {
      const root = scrollRef.current?.querySelector<HTMLElement>(`[data-text-block][data-block-id="${CSS.escape(segment.blockId)}"]`);
      const range = root ? rangeFromOffsets(root, segment.startOffset, segment.endOffset) : null;
      return range ? [range] : [];
    });
    if (ranges.length) api.registry.set(name, new api.HighlightConstructor(...ranges));
    return () => { api.registry.delete(name); };
  }, [activeSelection, blocks]);
  function setTypography(nextSize: number, nextLineHeight = lineHeight) {
    setFontSize(nextSize);
    setLineHeight(nextLineHeight);
    localStorage.setItem("frontier-text-font-size", String(nextSize));
    localStorage.setItem("frontier-text-line-height", String(nextLineHeight));
  }
  function jumpMatch(next: number) {
    if (!matches.length) return;
    const index = (next + matches.length) % matches.length;
    setMatchIndex(index);
    const match = matches[index];
    const target = match.kind === "asset"
      ? scrollRef.current?.querySelector(`[data-block-id="${match.blockId}"]`)
      : scrollRef.current?.querySelector(`[data-search-key="${match.blockId}:${match.offset}"]`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function capture(anchor?: { x: number; y: number }) {
    const native = window.getSelection();
    if (!native || !native.toString().trim() || native.rangeCount === 0 || !scrollRef.current) return;
    const range = native.getRangeAt(0);
    const selected = textSelectionFromRange(scrollRef.current, range);
    if (!selected) return;
    const first = selected.segments[0];
    onSelection({
      text: selected.text,
      page: selected.page,
      blockId: first.blockId,
      startOffset: first.startOffset,
      endOffset: first.endOffset,
      segments: selected.segments,
      rect: selected.rect,
      anchor: anchor ?? { x: selected.rect.right, y: selected.rect.bottom },
      pageContext: selected.pageContext,
    });
    // Persist one visual selection layer after React opens the toolbar. This also
    // suppresses Edge's native menu on mobile without leaving a double highlight.
    window.setTimeout(() => native.removeAllRanges(), 0);
  }
  function beginMobileSelection(event: React.PointerEvent<HTMLDivElement>) {
    if (!mobile || event.pointerType === "mouse" || (event.target as Element).closest("button, input, [role=button]")) return;
    window.clearTimeout(longPressTimer.current);
    longPressStart.current = { x: event.clientX, y: event.clientY };
    longPressTimer.current = window.setTimeout(() => {
      const point = caretAtPoint(event.clientX, event.clientY);
      const element = point && (point.node.nodeType === Node.ELEMENT_NODE ? point.node as Element : point.node.parentElement);
      const block = element?.closest<HTMLElement>("[data-text-block]");
      if (!point || !block) return;
      const offset = offsetWithin(block, point.node, point.offset);
      if (offset == null) return;
      const bounds = wordBounds(block.textContent || "", offset);
      const range = rangeFromOffsets(block, bounds.start, bounds.end);
      if (!range) return;
      const rect = range.getBoundingClientRect(); const text = range.toString(); const fullText = block.textContent || "";
      if (!text.trim()) return;
      const blockId = block.dataset.blockId || "";
      onSelection({ text, page: Number(block.dataset.pageNumber || 1), blockId, startOffset: bounds.start, endOffset: bounds.end, segments: [{ blockId, startOffset: bounds.start, endOffset: bounds.end }], rect, anchor: { x: rect.right, y: rect.bottom }, pageContext: fullText.slice(Math.max(0, bounds.start - 300), bounds.end + 300) });
      navigator.vibrate?.(12);
    }, 420);
  }
  function trackMobileSelection(event: React.PointerEvent<HTMLDivElement>) {
    const start = longPressStart.current;
    if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) cancelMobileSelection();
  }
  function cancelMobileSelection() { window.clearTimeout(longPressTimer.current); longPressStart.current = null; }

  if (!document || document.status === "processing" || document.status === "missing") return <DocumentConversionProgress document={document} onRetry={onRetry} />;
  if (document.status === "failed") return <Stack alignItems="center" justifyContent="center" sx={{ flex: 1, p: 3 }}><ErrorState error={document.error || "文本模式生成失败"} /><Button variant="outlined" onClick={document.retryable === false ? onModeToggle : onRetry}>{document.retryable === false ? "使用 PDF 模式" : "重新生成"}</Button></Stack>;

  return <Box sx={{ position: "relative", flex: 1, minHeight: 0, overflow: "hidden", bgcolor: "#f7f7f5" }}>
    {toolsOpen && <TextToolbar mobile={mobile} search={search} matches={matches.length} matchIndex={matchIndex} fontSize={fontSize} lineHeight={lineHeight} onSearch={setSearch} onMatch={jumpMatch} onFontSize={(value) => setTypography(value)} onLineHeight={(value) => setTypography(fontSize, value)} onModeToggle={onModeToggle} />}
    <Box ref={scrollRef} className={mobile ? "mobile-reader-selection" : "desktop-reader-selection"} onContextMenu={mobile ? (event) => event.preventDefault() : undefined} onPointerDown={beginMobileSelection} onPointerMove={trackMobileSelection} onPointerUp={cancelMobileSelection} onPointerCancel={cancelMobileSelection} onMouseUp={!mobile ? (event) => capture({ x: event.clientX, y: event.clientY }) : undefined} sx={{ height: "100%", overflowY: "auto", WebkitOverflowScrolling: "touch", touchAction: "pan-y", px: { xs: 2, sm: 3 }, py: { xs: 2.5, sm: 4 } }}>
      <Box component="article" lang="en" sx={{ mx: "auto", maxWidth: 820, p: { xs: 2, sm: 5 }, bgcolor: "background.paper", borderRadius: { xs: 1.5, sm: 2 }, boxShadow: { xs: "none", sm: "0 8px 36px rgba(25,35,45,.08)" }, fontFamily: 'Georgia, "Noto Serif SC", "Songti SC", serif', fontSize, lineHeight, hyphens: "auto", WebkitHyphens: "auto", wordBreak: "normal", overflowWrap: "break-word", "& p": { mt: 0, mb: "1lh" }, "& mark.text-reader-search, & .katex.text-reader-search": { bgcolor: "rgba(255,193,7,.24)", color: "inherit", borderRadius: "1px" } }}>
        <ReaderBlocks blocks={blocks} scrollRoot={scrollRef} annotations={annotations} activeSelection={activeSelection} search={search} onOpenAsset={setAsset} />
      </Box>
    </Box>
    <AssetViewer asset={asset} onClose={() => setAsset(null)} />
  </Box>;
}

function ReaderBlocks({ blocks, scrollRoot, annotations, activeSelection, search, onOpenAsset }: { blocks: DocumentBlock[]; scrollRoot: React.RefObject<HTMLDivElement | null>; annotations: Annotation[]; activeSelection?: TextReaderActiveSelection; search: string; onOpenAsset: (asset: { url?: string; html?: string; title: string }) => void }) {
  const rendered: React.ReactNode[] = [];
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];
    if (block.kind.toLowerCase() === "bibliography_header") {
      const items: DocumentBlock[] = [];
      while (index + 1 < blocks.length && blocks[index + 1].kind.toLowerCase() === "bibliography_item") items.push(blocks[++index]);
      rendered.push(<ReferencesSection key={block.id} header={block} items={items} blocks={blocks} scrollRoot={scrollRoot} annotations={annotations} activeSelection={activeSelection} search={search} onOpenAsset={onOpenAsset} />);
      continue;
    }
    if (block.kind.toLowerCase() === "bibliography_item") continue;
    rendered.push(<DocumentBlockView block={block} blocks={blocks} scrollRoot={scrollRoot} annotations={annotations} activeSelection={activeSelection} search={search} key={block.id} onOpenAsset={onOpenAsset} />);
  }
  return rendered;
}

function ReferencesSection({ header, items, blocks, scrollRoot, annotations, activeSelection, search, onOpenAsset }: { header: DocumentBlock; items: DocumentBlock[]; blocks: DocumentBlock[]; scrollRoot: React.RefObject<HTMLDivElement | null>; annotations: Annotation[]; activeSelection?: TextReaderActiveSelection; search: string; onOpenAsset: (asset: { url?: string; html?: string; title: string }) => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const reveal = (event: Event) => {
      const target = (event as CustomEvent<{ blockId?: string }>).detail?.blockId;
      if (!target || items.some((item) => item.id === target)) setOpen(true);
    };
    document.addEventListener("frontier-open-references", reveal);
    return () => document.removeEventListener("frontier-open-references", reveal);
  }, [items]);
  useEffect(() => { if (search.trim().length > 1 && items.some((item) => `${item.text || ""} ${item.caption || ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))) setOpen(true); }, [items, search]);
  return <Box id={header.anchor ? `reader-${header.anchor}` : undefined} data-reader-anchor={header.anchor || undefined} sx={{ my: 3, borderTop: 1, borderBottom: 1, borderColor: "divider" }}><Button fullWidth onClick={() => setOpen((value) => !value)} endIcon={<ExpandMoreRounded sx={{ transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .18s ease" }} />} sx={{ justifyContent: "space-between", px: 0, py: 1.5, color: "text.primary", fontFamily: "inherit", fontSize: "1.25em", fontWeight: 750, "& .MuiButton-endIcon": { ml: "auto" } }}>参考文献 · {items.length} 条</Button>{open && <Box component="ol" sx={{ m: 0, pb: 2, pl: { xs: 3, sm: 4 }, "& > li": { mb: 1.15 } }}>{items.map((item) => <DocumentBlockView block={item} blocks={blocks} scrollRoot={scrollRoot} annotations={annotations} activeSelection={activeSelection} search={search} key={item.id} onOpenAsset={onOpenAsset} />)}</Box>}</Box>;
}

function DocumentConversionProgress({ document, onRetry }: { document: PaperDocument | undefined; onRetry: () => void }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  const elapsed = document?.started_at ? Math.max(0, Math.round((now - new Date(document.started_at).getTime()) / 1000)) : 0;
  const progress = document?.progress ?? 3;
  const detail = document?.phase === "assets" && document.total_items
    ? `已处理 ${document.processed_items || 0}/${document.total_items} 个内容块`
    : document?.page_count ? `共 ${document.page_count} 页` : "正在启动转换任务";
  const estimate = document?.estimated_seconds_low && document?.estimated_seconds_high
    ? `通常需要 ${formatDuration(document.estimated_seconds_low)}–${formatDuration(document.estimated_seconds_high)}`
    : "首次转换时间取决于论文页数和服务器性能";
  const noHeartbeat = Boolean(document?.started_at) && elapsed >= 90 && document?.phase === "queued";
  return <Stack alignItems="center" justifyContent="center" sx={{ flex: 1, bgcolor: "background.default", p: 3 }}><Stack spacing={1.5} sx={{ width: "min(420px, 100%)" }}><Box><Typography variant="h6">{noHeartbeat ? "转换任务没有启动" : "正在生成文本模式"}</Typography><Typography variant="body2" color={noHeartbeat ? "error" : "text.secondary"} sx={{ mt: .5 }}>{noHeartbeat ? "后台任务超过 90 秒没有发回状态，请检查服务日志或重新生成。" : document?.phase_label || "正在准备转换任务"}</Typography></Box><LinearProgress variant="determinate" color={noHeartbeat ? "error" : "primary"} value={Math.min(99, progress)} sx={{ height: 7, borderRadius: 999 }} /><Stack direction="row" justifyContent="space-between"><Typography variant="caption" color="text.secondary">{document?.stage ? `第 ${document.stage}/${document.stage_count || 4} 阶段 · ` : ""}{progress}%</Typography><Typography variant="caption" color="text.secondary">已耗时 {formatDuration(elapsed)}</Typography></Stack><Typography variant="body2">{detail}</Typography><Typography variant="caption" color="text.secondary">{estimate}。版面解析阶段无法提供可信的逐页进度，进度会在阶段完成后继续更新。</Typography>{noHeartbeat && <Button variant="outlined" onClick={onRetry}>重新生成</Button>}</Stack></Stack>;
}

function formatDuration(seconds: number) { if (seconds < 60) return `${seconds} 秒`; const minutes = Math.floor(seconds / 60); const rest = seconds % 60; return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`; }

function TextToolbar({ mobile, search, matches, matchIndex, fontSize, lineHeight, onSearch, onMatch, onFontSize, onLineHeight, onModeToggle }: { mobile: boolean; search: string; matches: number; matchIndex: number; fontSize: number; lineHeight: number; onSearch: (value: string) => void; onMatch: (index: number) => void; onFontSize: (value: number) => void; onLineHeight: (value: number) => void; onModeToggle: () => void }) {
  const controlSx = mobile ? compactReaderControlSx : readerControlSx;
  return <Paper elevation={mobile ? 3 : 0} square sx={{ position: mobile ? "absolute" : "relative", zIndex: 6, top: 0, left: 0, right: 0, p: 1, px: { xs: 1, sm: 1.5 }, borderBottom: 1, borderColor: "divider" }}><Box sx={{ display: "grid", gridTemplateColumns: { xs: "auto minmax(0, 1fr)", sm: "auto minmax(0, 1fr) auto" }, gridTemplateAreas: { xs: '"search search" "mode controls"', sm: '"mode search controls"' }, gap: 1, alignItems: "center" }}><IconButton aria-label="切换到 PDF 模式" title="切换到 PDF 模式" onClick={onModeToggle} sx={{ ...controlSx, gridArea: "mode" }}><PictureAsPdfRounded sx={{ fontSize: mobile ? 18 : 20 }} /></IconButton><TextField size="small" value={search} onChange={(event) => onSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onMatch(matchIndex + (event.shiftKey ? -1 : 1)); }} placeholder="搜索全文" sx={{ ...readerSearchFieldSx, gridArea: "search", width: "100%", minWidth: 0 }} InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded fontSize="small" /></InputAdornment>, endAdornment: <InputAdornment position="end"><Typography variant="caption">{matches ? matchIndex + 1 : 0}/{matches}</Typography><IconButton size="small" disabled={!matches} onClick={() => onMatch(matchIndex - 1)} sx={compactReaderControlSx}><KeyboardArrowUpRounded fontSize="small" /></IconButton><IconButton size="small" disabled={!matches} onClick={() => onMatch(matchIndex + 1)} sx={compactReaderControlSx}><KeyboardArrowDownRounded fontSize="small" /></IconButton></InputAdornment> }} /><Stack direction="row" justifyContent="flex-end" alignItems="center" spacing={1} sx={{ gridArea: "controls", minWidth: 0 }}><FormControl size="small"><Select value={fontSize} onChange={(event) => onFontSize(Number(event.target.value))} aria-label="正文字号" sx={{ ...readerSelectSx, minWidth: 82 }}>{[14, 15, 16, 17, 18].map((value) => <MenuItem key={value} value={value}>{value}px</MenuItem>)}</Select></FormControl><FormControl size="small"><Select value={lineHeight} onChange={(event) => onLineHeight(Number(event.target.value))} aria-label="正文行距" sx={{ ...readerSelectSx, minWidth: 108 }}>{[1.45, 1.6, 1.75, 1.9, 2.05].map((value) => <MenuItem key={value} value={value}>行距 {value}</MenuItem>)}</Select></FormControl></Stack></Box></Paper>;
}

function DocumentBlockView({ block, blocks, scrollRoot, annotations, activeSelection, search, onOpenAsset }: { block: DocumentBlock; blocks: DocumentBlock[]; scrollRoot: React.RefObject<HTMLDivElement | null>; annotations: Annotation[]; activeSelection?: TextReaderActiveSelection; search: string; onOpenAsset: (asset: { url?: string; html?: string; title: string }) => void }) {
  const kind = block.kind.toLowerCase();
  const anchorProps = { id: block.anchor ? `reader-${block.anchor}` : undefined, "data-reader-anchor": block.anchor || undefined };
  const captionId = `${block.id}:caption`;
  const blockAnnotations = annotations.flatMap((item) => {
    const segment = item.position_json.segments?.find((value) => value.block_id === block.id);
    if (segment) return [{ ...item, position_json: { ...item.position_json, block_id: block.id, start_offset: segment.start_offset, end_offset: segment.end_offset } }];
    if (item.position_json.block_id === block.id) return [item];
    if (item.position_json.block_id || item.page_number !== block.page_number || !item.selected_text) return [];
    const selected = item.selected_text.trim();
    const start = (block.text || "").indexOf(selected);
    if (start < 0) return [];
    return [{ ...item, position_json: { ...item.position_json, block_id: block.id, start_offset: start, end_offset: start + selected.length } }];
  });
  const activeBlockSegment = activeSelection?.segments?.find((value) => value.blockId === block.id)
    ?? (activeSelection?.blockId === block.id && activeSelection.startOffset != null && activeSelection.endOffset != null ? activeSelection : undefined);
  if (activeBlockSegment && !customHighlightApi()) blockAnnotations.push({ id: "active-selection", paper_id: "", kind: "highlight", page_number: block.page_number, position_json: { block_id: block.id, start_offset: activeBlockSegment.startOffset, end_offset: activeBlockSegment.endOffset }, color: "#9CCCF4", created_at: "" });
  const captionAnnotations = annotations.flatMap((item) => {
    const segment = item.position_json.segments?.find((value) => value.block_id === captionId);
    if (segment) return [{ ...item, position_json: { ...item.position_json, block_id: captionId, start_offset: segment.start_offset, end_offset: segment.end_offset } }];
    if (item.position_json.block_id === captionId) return [item];
    if (item.position_json.block_id || item.page_number !== block.page_number || !item.selected_text || !block.caption) return [];
    const selected = item.selected_text.trim();
    const start = block.caption.indexOf(selected);
    if (start < 0) return [];
    return [{ ...item, position_json: { ...item.position_json, block_id: captionId, start_offset: start, end_offset: start + selected.length } }];
  });
  const activeCaptionSegment = activeSelection?.segments?.find((value) => value.blockId === captionId)
    ?? (activeSelection?.blockId === captionId && activeSelection.startOffset != null && activeSelection.endOffset != null ? activeSelection : undefined);
  if (activeCaptionSegment && !customHighlightApi()) captionAnnotations.push({ id: "active-caption-selection", paper_id: "", kind: "highlight", page_number: block.page_number, position_json: { block_id: captionId, start_offset: activeCaptionSegment.startOffset, end_offset: activeCaptionSegment.endOffset }, color: "#9CCCF4", created_at: "" });
  if (kind === "caption") return null;
  if (kind === "formula" && block.html) return <Box {...anchorProps} data-text-block data-block-id={block.id} data-page-number={block.page_number} onClick={(event) => followRichReference(event, blocks, scrollRoot.current)} sx={{ my: 2, overflowX: "auto", py: .5, textAlign: "center", "& .katex-display": { m: 0 }, "& .ltx_eqn_table": { width: "100%" } }} dangerouslySetInnerHTML={{ __html: renderArxivHtml(block.html, blockAnnotations, search, block.id) }} />;
  if (["picture", "table", "formula"].includes(kind)) {
    const captionRich = block.caption_html ? renderArxivHtml(block.caption_html, captionAnnotations, search, captionId) : null;
    const captionContent = !captionRich && block.caption ? renderText(block.caption, captionAnnotations, search, captionId, (reference) => jumpReference(reference, blocks, scrollRoot.current)) : null;
    const captionProps = captionRich ? { onClick: (event: React.MouseEvent<HTMLElement>) => followRichReference(event, blocks, scrollRoot.current), dangerouslySetInnerHTML: { __html: captionRich } } : {};
    return <Box {...anchorProps} component="figure" data-block-id={block.id} data-page-number={block.page_number} sx={{ m: 0, my: kind === "formula" ? 2 : 3 }}><Button onClick={() => onOpenAsset({ url: block.image_url, html: block.html, title: kind === "table" ? "查看表格" : kind === "formula" ? "查看公式" : "查看图片" })} sx={{ display: "block", width: "100%", p: 0, overflow: "hidden", bgcolor: kind === "formula" ? "transparent" : "#f3f5f7", border: kind === "formula" ? 0 : 1, borderColor: "divider", "&:hover": { bgcolor: kind === "formula" ? "action.hover" : "#eef2f6" } }}>{block.image_url ? <Box component="img" src={block.image_url} alt={block.caption || (kind === "table" ? "论文表格" : kind === "formula" ? "数学公式" : "论文图片")} sx={{ display: "block", width: kind === "formula" ? "auto" : "100%", maxWidth: "100%", mx: "auto", maxHeight: kind === "formula" ? 150 : 520, objectFit: "contain" }} /> : block.html ? <Box sx={{ width: "100%", overflow: "hidden", p: 1.5, color: "text.primary", textAlign: "left", fontFamily: '"Times New Roman", Times, serif', "& table": { width: "100%", borderCollapse: "collapse", fontFamily: "inherit" }, "& td, & th": { border: "1px solid", borderColor: "divider", p: .75, fontFamily: "inherit" } }} dangerouslySetInnerHTML={{ __html: renderArxivHtml(block.html) }} /> : null}</Button>{block.caption && <Typography data-text-block data-block-id={captionId} data-page-number={block.page_number} component="figcaption" variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, textAlign: { xs: "left", sm: "center" }, hyphens: "auto", WebkitHyphens: "auto", fontFamily: "inherit", lineHeight: 1.55, "& .katex": { fontSize: "1em" } }} {...captionProps}>{captionRich ? undefined : captionContent}</Typography>}</Box>;
  }
  const headingLevel = kind.includes("title") ? 1 : kind.includes("section") || kind.includes("heading") ? 2 : 0;
  const content = renderText(block.text || "", blockAnnotations, search, block.id, (reference) => jumpReference(reference, blocks, scrollRoot.current));
  const rich = block.html ? renderArxivHtml(block.html, blockAnnotations, search, block.id) : null;
  const richProps = rich ? { onClick: (event: React.MouseEvent<HTMLElement>) => followRichReference(event, blocks, scrollRoot.current), dangerouslySetInnerHTML: { __html: rich } } : {};
  if (headingLevel) return <Typography {...anchorProps} data-text-block data-block-id={block.id} data-page-number={block.page_number} component={headingLevel === 1 ? "h1" : "h2"} sx={{ mt: headingLevel === 1 ? 1 : 3.5, mb: 1.5, fontSize: headingLevel === 1 ? "1.7em" : "1.3em", lineHeight: 1.35, fontWeight: 750, fontFamily: "inherit" }} {...richProps}>{rich ? undefined : content}</Typography>;
  if (kind.includes("list") || kind === "bibliography_item") return <Typography {...anchorProps} data-text-block data-block-id={block.id} data-page-number={block.page_number} component="li" sx={{ mb: 1, pl: .5, fontFamily: "inherit", fontSize: kind === "bibliography_item" ? ".88em" : "inherit", lineHeight: 1.55, "& .ltx_bib_key": { display: "none" }, "& .ltx_bibblock": { display: "inline" }, "& .ltx_bibblock + .ltx_bibblock": { ml: .5 } }} {...richProps}>{rich ? undefined : content}</Typography>;
  return <Typography {...anchorProps} data-text-block data-block-id={block.id} data-page-number={block.page_number} component="p" sx={{ textAlign: { xs: "left", sm: "justify" }, hyphens: "auto", WebkitHyphens: "auto", overflowWrap: "break-word", wordBreak: "normal", fontFamily: "inherit", fontSize: "inherit", lineHeight: "inherit", "& a": { color: "primary.main", textDecorationColor: "rgba(25,118,210,.4)", cursor: "pointer" }, "& .katex": { fontSize: "1em" } }} {...richProps}>{rich ? undefined : content}</Typography>;
}

function renderArxivHtml(html: string, annotations: Annotation[] = [], search = "", blockId = "") {
  const clean = DOMPurify.sanitize(html, { ADD_ATTR: ["data-reader-ref", "data-reader-tex", "data-reader-display"] });
  const document = new DOMParser().parseFromString(`<div id="reader-fragment">${clean}</div>`, "text/html");
  const root = document.getElementById("reader-fragment");
  if (!root) return "";
  root.querySelectorAll<HTMLElement>("[data-reader-tex]").forEach((placeholder) => {
    const tex = placeholder.dataset.readerTex || "";
    try {
      placeholder.innerHTML = katex.renderToString(tex, {
        displayMode: placeholder.dataset.readerDisplay === "block",
        throwOnError: false,
        strict: "ignore",
        output: "htmlAndMathml",
      });
      placeholder.classList.add("reader-katex");
    } catch {
      placeholder.textContent = tex;
    }
  });
  const marks: Array<{ start: number; end: number; color?: string; search?: boolean }> = [];
  const fullText = root.textContent || "";
  for (const annotation of annotations) {
    let start = annotation.position_json.start_offset;
    let end = annotation.position_json.end_offset;
    if ((start == null || end == null || end <= start) && annotation.selected_text) {
      start = fullText.indexOf(annotation.selected_text);
      end = start >= 0 ? start + annotation.selected_text.length : start;
    }
    if (start != null && end != null && start >= 0 && end > start) marks.push({ start, end, color: annotation.color || "#F3D76B" });
  }
  const token = search.trim().toLocaleLowerCase();
  if (token.length > 1) {
    const lower = fullText.toLocaleLowerCase();
    let cursor = 0;
    while ((cursor = lower.indexOf(token, cursor)) >= 0) {
      marks.push({ start: cursor, end: cursor + token.length, search: true });
      cursor += token.length;
    }
  }
  if (marks.length) applyRichMarks(document, root, marks, blockId);
  return DOMPurify.sanitize(root.innerHTML, { ADD_ATTR: ["data-reader-ref", "aria-hidden"] });
}

function applyRichMarks(document: Document, root: HTMLElement, marks: Array<{ start: number; end: number; color?: string; search?: boolean }>, blockId: string) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let current: Node | null;
  while ((current = walker.nextNode())) nodes.push(current as Text);
  let offset = 0;
  for (const node of nodes) {
    const text = node.nodeValue || "";
    const nodeStart = offset;
    const nodeEnd = offset + text.length;
    offset = nodeEnd;
    const relevant = marks.filter((mark) => mark.start < nodeEnd && mark.end > nodeStart);
    if (!relevant.length || !node.parentNode) continue;
    const katexRoot = node.parentElement?.closest<HTMLElement>(".katex");
    if (katexRoot) {
      const searchMark = relevant.find((mark) => mark.search);
      const annotation = relevant.find((mark) => !mark.search);
      if (searchMark && !katexRoot.dataset.searchKey) {
        katexRoot.dataset.searchKey = `${blockId}:${searchMark.start}`;
        katexRoot.classList.add("text-reader-search");
      }
      if (annotation?.color) katexRoot.style.backgroundColor = `${annotation.color}80`;
      continue;
    }
    const boundaries = new Set([0, text.length]);
    relevant.forEach((mark) => { boundaries.add(Math.max(0, mark.start - nodeStart)); boundaries.add(Math.min(text.length, mark.end - nodeStart)); });
    const points = [...boundaries].sort((a, b) => a - b);
    const fragment = document.createDocumentFragment();
    points.slice(0, -1).forEach((start, index) => {
      const end = points[index + 1];
      const active = relevant.filter((mark) => mark.start <= nodeStart + start && mark.end >= nodeStart + end);
      const chunk = document.createTextNode(text.slice(start, end));
      if (!active.length) return fragment.appendChild(chunk);
      const mark = document.createElement("mark");
      const annotation = active.find((item) => !item.search);
      if (active.some((item) => item.search)) {
        mark.className = "text-reader-search";
        mark.dataset.searchKey = `${blockId}:${nodeStart + start}`;
      }
      if (annotation?.color) mark.style.backgroundColor = `${annotation.color}80`;
      mark.appendChild(chunk);
      fragment.appendChild(mark);
    });
    node.parentNode.replaceChild(fragment, node);
  }
}

function followRichReference(event: React.MouseEvent<HTMLElement>, blocks: DocumentBlock[], root: HTMLDivElement | null) {
  const link = (event.target as Element | null)?.closest<HTMLAnchorElement>("a[data-reader-ref]");
  if (!link) return;
  event.preventDefault();
  const anchor = link.dataset.readerRef;
  const target = blocks.find((block) => block.anchor === anchor);
  if (target?.kind.toLowerCase() === "bibliography_item") document.dispatchEvent(new CustomEvent("frontier-open-references", { detail: { blockId: target.id } }));
  if (target) window.setTimeout(() => root?.querySelector(`[data-block-id="${target.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" }), target.kind.toLowerCase() === "bibliography_item" ? 60 : 0);
}

function renderText(text: string, annotations: Annotation[], search: string, blockId: string, onReference: (reference: string) => void) {
  const searchToken = search.trim().toLocaleLowerCase();
  const boundaries = new Set([0, text.length]);
  const marks: Array<{ start: number; end: number; color?: string; search?: boolean }> = [];
  for (const annotation of annotations) {
    const start = annotation.position_json.start_offset ?? 0;
    const end = annotation.position_json.end_offset ?? start;
    if (end > start) { boundaries.add(start); boundaries.add(end); marks.push({ start, end, color: annotation.color || "#F3D76B" }); }
  }
  if (searchToken.length > 1) {
    const lower = text.toLocaleLowerCase(); let cursor = 0;
    while ((cursor = lower.indexOf(searchToken, cursor)) >= 0) { const end = cursor + searchToken.length; boundaries.add(cursor); boundaries.add(end); marks.push({ start: cursor, end, search: true }); cursor = end; }
  }
  const points = [...boundaries].sort((a, b) => a - b);
  return points.slice(0, -1).flatMap((start, index) => { const end = points[index + 1]; const active = marks.filter((mark) => mark.start <= start && mark.end >= end); const annotation = active.find((mark) => !mark.search); const isSearch = active.some((mark) => mark.search); const chunk = text.slice(start, end); const fragments = splitReferences(chunk, `${start}-${end}`, onReference); if (!annotation && !isSearch) return fragments; return <mark key={`${start}-${end}`} data-search-key={isSearch ? `${blockId}:${start}` : undefined} className={isSearch ? "text-reader-search" : undefined} style={{ backgroundColor: annotation?.color ? `${annotation.color}80` : undefined, color: "inherit", padding: 0 }}>{fragments}</mark>; });
}

function collectMatches(blocks: DocumentBlock[], search: string) { const token = search.trim().toLocaleLowerCase(); if (token.length < 2) return [] as Array<{ blockId: string; offset: number; kind: "text" | "asset" }>; return blocks.flatMap((block) => { if (block.kind.toLowerCase() === "caption") return []; const asset = ["picture", "table"].includes(block.kind.toLowerCase()); const result: Array<{ blockId: string; offset: number; kind: "text" | "asset" }> = []; const caption = (block.caption || "").toLocaleLowerCase(); let captionCursor = 0; while ((captionCursor = caption.indexOf(token, captionCursor)) >= 0) { result.push({ blockId: `${block.id}:caption`, offset: captionCursor, kind: "text" }); captionCursor += token.length; } const text = (block.html && !asset ? arxivTextContent(block.html) : block.text || "").toLocaleLowerCase(); let cursor = 0; while ((cursor = text.indexOf(token, cursor)) >= 0) { result.push({ blockId: block.id, offset: asset ? 0 : cursor, kind: asset ? "asset" : "text" }); if (asset) break; cursor += token.length; } return result; }); }

function arxivTextContent(html: string) {
  const parsed = new DOMParser().parseFromString(`<div>${renderArxivHtml(html)}</div>`, "text/html");
  return parsed.body.textContent || "";
}

function splitReferences(text: string, key: string, onReference: (reference: string) => void) {
  const pattern = /\b(?:section|sec\.?|figure|fig\.?|table|appendix|eq\.?|equation)\s*(?:\d+(?:\.\d+)*|[A-Z]\d*(?:\.\d+)*)\b/gi;
  const nodes: React.ReactNode[] = []; let cursor = 0; let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) { nodes.push(text.slice(cursor, match.index)); const reference = match[0]; nodes.push(<Box component="button" type="button" key={`${key}-${match.index}`} onClick={() => onReference(reference)} sx={{ display: "inline", border: 0, p: 0, bgcolor: "transparent", color: "primary.main", font: "inherit", textDecoration: "underline", textUnderlineOffset: "2px", cursor: "pointer" }}>{reference}</Box>); cursor = match.index + reference.length; }
  nodes.push(text.slice(cursor)); return nodes;
}

function jumpReference(reference: string, blocks: DocumentBlock[], root: HTMLDivElement | null) {
  const normalized = reference.toLocaleLowerCase().replace(/\s+/g, " ").replace(/\bsec\.?\b/, "section").replace(/\bfig\.?\b/, "figure").replace(/\beq\.?\b/, "equation");
  const number = normalized.match(/(?:section|figure|table|appendix|equation)\s*([a-z]?\d+(?:\.\d+)*)/)?.[1];
  if (!number) return;
  const target = blocks.find((item) => {
    const haystack = `${item.caption || ""} ${item.text || ""}`.toLocaleLowerCase();
    if (normalized.startsWith("section")) return item.kind.toLowerCase().includes("section") && new RegExp(`^\\s*${number.replaceAll(".", "\\.")}\\b`, "i").test(item.text || "");
    if (normalized.startsWith("appendix")) return item.kind.toLowerCase().includes("section") && new RegExp(`^\\s*(?:appendix\\s*)?${number}`, "i").test(item.text || "");
    const kind = normalized.startsWith("figure") ? "figure" : normalized.startsWith("table") ? "table" : "equation";
    return new RegExp(`\\b${kind}\\s*${number.replaceAll(".", "\\.")}\\b`, "i").test(haystack);
  });
  if (target) root?.querySelector(`[data-block-id="${target.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function AssetViewer({ asset, onClose }: { asset: { url?: string; html?: string; title: string } | null; onClose: () => void }) {
  return <Dialog open={Boolean(asset)} onClose={onClose} fullScreen><DialogTitle sx={{ pr: 7 }}><Stack direction="row" spacing={1.25} alignItems="baseline"><Box component="span">{asset?.title}</Box>{asset?.url && <Typography component="span" variant="caption" color="text.secondary">滚轮缩放 · 拖动查看局部</Typography>}</Stack><IconButton onClick={onClose} sx={{ position: "absolute", right: 12, top: 10 }}><CloseRounded /></IconButton></DialogTitle><DialogContent sx={{ display: "grid", placeItems: "center", bgcolor: "#e9edf2", p: 1 }}>{asset?.url ? <TransformWrapper minScale={.7} initialScale={1} maxScale={8} centerOnInit centerZoomedOut smooth wheel={{ step: .0025 }} panning={{ velocityDisabled: true }}><TransformComponent wrapperStyle={{ width: "100%", height: "calc(100dvh - 72px)" }} contentStyle={{ width: "100%", height: "100%", alignItems: "center", justifyContent: "center" }}><img src={asset.url} alt={asset.title} draggable={false} style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", userSelect: "none" }} /></TransformComponent></TransformWrapper> : <Box sx={{ width: "100%", height: "100%", overflow: "auto", bgcolor: "white", p: 2, fontFamily: '"Times New Roman", Times, serif', "& table": { borderCollapse: "collapse", minWidth: "100%", fontFamily: "inherit" }, "& td, & th": { border: "1px solid #ccd3db", p: 1, fontFamily: "inherit" } }} dangerouslySetInnerHTML={{ __html: renderArxivHtml(asset?.html || "") }} />}</DialogContent></Dialog>;
}
