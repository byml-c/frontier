import AddRounded from "@mui/icons-material/AddRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DescriptionRounded from "@mui/icons-material/DescriptionRounded";
import LightbulbRounded from "@mui/icons-material/LightbulbRounded";
import PsychologyRounded from "@mui/icons-material/PsychologyRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import { Box, Button, Chip, Divider, Drawer, FormControl, IconButton, InputAdornment, Link as MuiLink, List, ListItemButton, ListItemText, MenuItem, Paper, Select, Stack, Tab, Tabs, TextField, Typography, useMediaQuery, useTheme } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, formatDate } from "../api";
import { mergeGraphViewportState, readGraphViewportState, writeGraphViewportState } from "../components/graphViewportStorage";
import { PaperTitle } from "../components/PaperTitle";
import { Empty, ErrorState, Loading } from "../components/States";
import { templateSurfaces } from "../theme";
import type { GraphData, ResearchNode } from "../types";

type ForceNode = ResearchNode & { x?: number; y?: number; fx?: number; fy?: number; virtual?: boolean; connects_to?: string[] };
type ForceLink = { id: string; source: string | ForceNode; target: string | ForceNode };
const ZOOM_PRESETS = [50, 75, 100, 125, 150, 200];

function clampZoom(value: number): number { return Math.max(35, Math.min(300, Math.round(value / 5) * 5)); }
// force-graph treats an exact initial scale of 1 as "untouched" and replaces it
// with an automatic node-count scale. A sub-pixel epsilon opts out without a
// visible difference; the toolbar continues to expose the canonical percentage.
function forceGraphScale(zoom: number): number { return Math.min(3, Math.max(.35, zoom) + .000001); }
function nodeBudgetForZoom(zoom: number): number {
  if (zoom < 75) return 24;
  if (zoom < 100) return 36;
  if (zoom < 125) return 48;
  if (zoom < 150) return 64;
  if (zoom < 200) return 80;
  return 100;
}

function stableNodePoint(index: number): { x: number; y: number } {
  if (index === 0) return { x: 0, y: 0 };
  const angle = index * Math.PI * (3 - Math.sqrt(5));
  const radius = 70 + Math.sqrt(index) * 125;
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

function mergeGraphWindow(current: GraphData | undefined, incoming: GraphData, limit: number): GraphData {
  if (!current) return incoming;
  const nodeMap = new Map<string, ResearchNode>();
  [...incoming.nodes, ...current.nodes].forEach((node) => { if (!nodeMap.has(node.id) && nodeMap.size < limit) nodeMap.set(node.id, node); });
  const selected = new Set(nodeMap.keys());
  const linkMap = new Map<string, GraphData["links"][number]>();
  [...incoming.links, ...current.links].forEach((link) => {
    if (selected.has(link.source_id) && selected.has(link.target_id)) linkMap.set(link.id, link);
  });
  const boundaryMap = new Map<string, NonNullable<GraphData["boundary_nodes"]>[number]>();
  [...(incoming.boundary_nodes ?? []), ...(current.boundary_nodes ?? [])].forEach((node) => {
    if (selected.has(node.id)) return;
    const connectsTo = node.connects_to.filter((id) => selected.has(id));
    if (connectsTo.length) boundaryMap.set(node.id, { ...node, connects_to: connectsTo });
  });
  return { focus_id: incoming.focus_id, nodes: [...nodeMap.values()], links: [...linkMap.values()], boundary_nodes: [...boundaryMap.values()], has_more: incoming.has_more || current.has_more };
}

export function GraphPage() {
  const { nodeId } = useParams(); const navigate = useNavigate(); const theme = useTheme(); const mobile = useMediaQuery(theme.breakpoints.down("md"));
  const graphRef = useRef<ForceGraphMethods<NodeObject<ForceNode>, LinkObject<ForceNode, ForceLink>> | undefined>(undefined); const containerRef = useRef<HTMLDivElement>(null); const viewportInitializedRef = useRef(false);
  const expandingRef = useRef(new Set<string>()); const dataRef = useRef<{ nodes: ForceNode[]; links: ForceLink[] }>({ nodes: [], links: [] });
  const storedViewportRef = useRef(readGraphViewportState());
  const [size, setSize] = useState({ width: 0, height: 0 }); const [search, setSearch] = useState(""); const [tab, setTab] = useState("overview"); const [zoomPercent, setZoomPercent] = useState(100); const [nodeLimit, setNodeLimit] = useState(() => nodeBudgetForZoom(100)); const [graphWindow, setGraphWindow] = useState<GraphData>(); const [viewportReady, setViewportReady] = useState(false);
  const subgraph = useQuery({ queryKey: ["subgraph", nodeLimit], queryFn: () => api<GraphData>(`/api/graph/subgraph?limit=${nodeLimit}`) });
  const detail = useQuery({ queryKey: ["node", nodeId], queryFn: () => api<ResearchNode>(`/api/graph/nodes/${nodeId}`), enabled: !!nodeId });
  const searchResults = useQuery({ queryKey: ["node-search", search], queryFn: () => api<ResearchNode[]>(`/api/graph/nodes?q=${encodeURIComponent(search)}&limit=8`), enabled: search.trim().length > 1 });
  useEffect(() => { if (subgraph.data) setGraphWindow((current) => mergeGraphWindow(current, subgraph.data, nodeLimit)); }, [nodeLimit, subgraph.data]);
  useEffect(() => { const desired = nodeBudgetForZoom(zoomPercent); if (desired > nodeLimit) setNodeLimit(desired); }, [nodeLimit, zoomPercent]);
  const data = useMemo(() => {
    const previous = new Map(dataRef.current.nodes.map((node) => [node.id, node]));
    const realNodes: ForceNode[] = (graphWindow?.nodes ?? []).map((node, index) => {
      const prior = previous.get(node.id); const stored = storedViewportRef.current.nodes[node.id]; const initial = stableNodePoint(index); const x = prior?.x ?? stored?.x ?? initial.x; const y = prior?.y ?? stored?.y ?? initial.y;
      return { ...node, x, y, fx: x, fy: y } satisfies ForceNode;
    });
    const realById = new Map(realNodes.map((node) => [node.id, node]));
    const centroid = realNodes.reduce((sum, node) => ({ x: sum.x + (node.x ?? 0), y: sum.y + (node.y ?? 0) }), { x: 0, y: 0 });
    if (realNodes.length) { centroid.x /= realNodes.length; centroid.y /= realNodes.length; }
    const boundaryNodes = (graphWindow?.boundary_nodes ?? []).map((node, index) => {
      const anchors = node.connects_to.map((id) => realById.get(id)).filter(Boolean) as ForceNode[];
      const anchor = anchors[0]; const ax = anchor?.x ?? centroid.x; const ay = anchor?.y ?? centroid.y;
      let dx = ax - centroid.x; let dy = ay - centroid.y; const length = Math.hypot(dx, dy);
      if (length < 20) { const angle = index * 2.399; dx = Math.cos(angle); dy = Math.sin(angle); } else { dx /= length; dy /= length; }
      const prior = previous.get(node.id); const x = prior?.x ?? ax + dx * 280; const y = prior?.y ?? ay + dy * 280;
      return { ...node, virtual: true, x, y, fx: x, fy: y } satisfies ForceNode;
    });
    const boundaryLinks = boundaryNodes.flatMap((node) => (node.connects_to ?? []).filter((id) => realById.has(id)).map((id) => ({ id: `boundary-${id}-${node.id}`, source: id, target: node.id })));
    return { nodes: [...realNodes, ...boundaryNodes], links: [...(graphWindow?.links ?? []).map((link) => ({ id: link.id, source: link.source_id, target: link.target_id })), ...boundaryLinks] as ForceLink[] };
  }, [graphWindow]);
  dataRef.current = data;
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const next = { width: Math.round(entry.contentRect.width), height: Math.round(entry.contentRect.height) };
      setSize((current) => current.width === next.width && current.height === next.height ? current : next);
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => { setTab("overview"); }, [nodeId]);
  const persistViewport = useCallback(() => {
    const graph = graphRef.current;
    if (!graph || size.width <= 0 || size.height <= 0) return;
    const center = graph.screen2GraphCoords(size.width / 2, size.height / 2);
    const positions = Object.fromEntries(dataRef.current.nodes.filter((node) => !node.virtual && Number.isFinite(node.x) && Number.isFinite(node.y)).map((node) => [node.id, { x: Number(node.x), y: Number(node.y) }]));
    const state = mergeGraphViewportState(storedViewportRef.current, center, positions);
    storedViewportRef.current = state;
    writeGraphViewportState(state);
  }, [size.height, size.width]);
  useLayoutEffect(() => {
    if (viewportInitializedRef.current || !graphWindow || !data.nodes.length || !graphRef.current || size.width <= 0 || size.height <= 0) return;
    let revealFrame = 0;
    const initialViewport = storedViewportRef.current;
    const transformFrame = requestAnimationFrame(() => {
      graphRef.current?.centerAt(initialViewport.center.x, initialViewport.center.y, 0);
      graphRef.current?.zoom(forceGraphScale(1), 0);
      revealFrame = requestAnimationFrame(() => {
        viewportInitializedRef.current = true;
        setViewportReady(true);
        setZoomPercent(100);
        persistViewport();
      });
    });
    return () => { cancelAnimationFrame(transformFrame); cancelAnimationFrame(revealFrame); };
  }, [data, graphWindow, nodeId, persistViewport, size.height, size.width]);
  function applyZoom(value: number) { const next = clampZoom(value); setZoomPercent(next); graphRef.current?.zoom(forceGraphScale(next / 100), 0); }
  async function expandBoundary(node: ForceNode) {
    if (!node.virtual || expandingRef.current.has(node.id)) return;
    expandingRef.current.add(node.id);
    try {
      const incoming = await api<GraphData>(`/api/graph/subgraph?focus_id=${node.id}&limit=${nodeLimit}`);
      setGraphWindow((current) => mergeGraphWindow(current, incoming, nodeLimit));
    } finally { expandingRef.current.delete(node.id); }
  }
  function loadVisibleBoundary() {
    const graph = graphRef.current; if (!graph) return;
    const topLeft = graph.screen2GraphCoords(0, 0); const bottomRight = graph.screen2GraphCoords(size.width, size.height);
    const visible = dataRef.current.nodes.find((node) => node.virtual && (node.x ?? 0) >= topLeft.x && (node.x ?? 0) <= bottomRight.x && (node.y ?? 0) >= topLeft.y && (node.y ?? 0) <= bottomRight.y);
    if (visible) void expandBoundary(visible);
  }
  const inspector = detail.isLoading ? <Loading /> : detail.error ? <ErrorState error={detail.error} /> : detail.data ? <NodeDetail node={detail.data} tab={tab} setTab={setTab} onClose={() => navigate("/graph")} /> : null;
  return <Box sx={{ position: "relative", display: "flex", height: "100%", minHeight: 0, overflow: "hidden", bgcolor: "background.default" }}>
    <Box ref={containerRef} sx={{ position: "relative", flex: 1, minWidth: 0, height: "100%" }}>
      <Paper elevation={0} sx={{ position: "absolute", zIndex: 4, left: { xs: 12, md: 24 }, right: { xs: 12, md: "auto" }, top: { xs: 12, md: 20 }, width: { md: 360 }, ...templateSurfaces.navigation }}><TextField fullWidth size="small" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索研究节点" InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded /></InputAdornment> }} />{searchResults.data && search.trim() && <List dense sx={{ pt: 1 }}>{searchResults.data.map((node) => <ListItemButton key={node.id} onClick={() => { navigate(`/graph/${node.id}`); setSearch(""); }}><ListItemText primary={node.name} secondary={node.core_question} /><ChevronRightRounded /></ListItemButton>)}</List>}</Paper>
      {subgraph.isLoading && !graphWindow ? <Loading label="正在加载研究图谱" /> : subgraph.error ? <Box sx={{ p: 3 }}><ErrorState error={subgraph.error} /></Box> : !data.nodes.length ? <Empty title="图谱还没有节点" body="Agent 建立研究节点后会显示在这里。" /> : size.width > 0 && size.height > 0 && <Box sx={{ position: "absolute", inset: 0, opacity: viewportReady ? 1 : 0 }}><ForceGraph2D ref={graphRef} width={size.width} height={size.height} graphData={data} backgroundColor="#FFFFFF" nodeRelSize={25} linkColor={(link) => String(link.id).startsWith("boundary-") ? "rgba(25, 118, 210, .12)" : "rgba(25, 118, 210, .22)"} linkWidth={1.2} linkDirectionalArrowLength={4} linkDirectionalArrowRelPos={0.86} minZoom={.35} maxZoom={3} enableZoomInteraction={false} cooldownTicks={1} d3AlphaDecay={1} d3VelocityDecay={1} onZoomEnd={({ k }) => { if (!viewportInitializedRef.current) return; setZoomPercent(clampZoom(k * 100)); persistViewport(); loadVisibleBoundary(); }} onNodeDragEnd={(node) => { node.fx = node.x; node.fy = node.y; persistViewport(); }} onEngineStop={() => graphRef.current?.pauseAnimation()} onNodeClick={(node) => { if (node.virtual) { void expandBoundary(node); return; } navigate(`/graph/${node.id}`); }} nodeCanvasObject={(node, ctx, scale) => drawNode(node, ctx, scale, node.id === nodeId)} nodePointerAreaPaint={(node, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(node.x ?? 0, node.y ?? 0, node.virtual ? 20 : 28, 0, Math.PI * 2); ctx.fill(); }} /></Box>}
      <ZoomToolbox value={zoomPercent} onChange={applyZoom} />
    </Box>
    {!mobile && nodeId && <Paper square elevation={2} sx={{ position: "absolute", zIndex: 5, inset: "0 0 0 auto", width: 430, borderLeft: 1, borderColor: "divider", overflowY: "auto", bgcolor: "rgba(255,255,255,.94)", backdropFilter: "blur(24px) saturate(150%)" }}>{inspector}</Paper>}
    {mobile && <Drawer anchor="bottom" open={!!nodeId} onClose={() => navigate("/graph")} PaperProps={{ sx: { height: "76dvh", borderRadius: "20px 20px 0 0" } }}>{inspector}</Drawer>}
  </Box>;
}

function drawNode(node: ForceNode, ctx: CanvasRenderingContext2D, scale: number, active: boolean) {
  const x = node.x ?? 0; const y = node.y ?? 0; const radius = node.virtual ? 18 : active ? 28 : 25 + Math.min(node.paper_count ?? 0, 8) * .5;
  ctx.save();
  ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
  if (node.virtual) { ctx.fillStyle = "rgba(255,255,255,.92)"; ctx.fill(); ctx.setLineDash([5 / scale, 4 / scale]); ctx.strokeStyle = "rgba(25,118,210,.48)"; ctx.lineWidth = 2 / scale; ctx.stroke(); }
  else { ctx.fillStyle = active ? "#0066CC" : node.color || "#1976D2"; ctx.fill(); }
  if (active) { ctx.beginPath(); ctx.arc(x, y, radius + 5, 0, Math.PI * 2); ctx.strokeStyle = "rgba(0, 114, 229, .22)"; ctx.lineWidth = 4; ctx.stroke(); }
  ctx.restore();
  const fontSize = 13 / scale; ctx.font = `${node.virtual ? 500 : 600} ${fontSize}px Inter, "Noto Sans SC", sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = node.virtual ? "#5f6b7a" : "#212121";
  const maxWidth = 170 / scale; let label = node.name; while (label.length > 2 && ctx.measureText(label).width > maxWidth) label = `${label.slice(0, -2)}…`;
  ctx.fillText(label, x, y + radius + 5 / scale);
}

function ZoomToolbox({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const options = [...new Set([...ZOOM_PRESETS, value])].sort((left, right) => left - right);
  const segment = { width: 38, height: 38, borderRadius: 0 } as const;
  return <Paper elevation={2} sx={{ position: "absolute", zIndex: 4, left: { xs: 12, md: 24 }, bottom: { xs: 12, md: 20 }, display: "flex", alignItems: "stretch", overflow: "hidden", border: 1, borderColor: "divider", borderRadius: 2 }}>
    <IconButton aria-label="缩小图谱" onClick={() => onChange(value - 5)} sx={segment}><RemoveRounded fontSize="small" /></IconButton>
    <FormControl size="small" sx={{ borderLeft: 1, borderRight: 1, borderColor: "divider" }}>
      <Select
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        onWheel={(event) => { event.preventDefault(); onChange(value + (event.deltaY < 0 ? 5 : -5)); }}
        renderValue={(selected) => `${selected}%`}
        inputProps={{ "aria-label": "图谱缩放百分比" }}
        sx={{
          width: 96,
          height: 38,
          borderRadius: 0,
          fontWeight: 650,
          "& .MuiSelect-select": { py: 1, pl: 2, pr: "32px !important", textAlign: "center" },
          "& .MuiOutlinedInput-notchedOutline": { border: 0 },
        }}
      >
        {options.map((preset) => <MenuItem value={preset} key={preset}>{preset}%</MenuItem>)}
      </Select>
    </FormControl>
    <IconButton aria-label="放大图谱" onClick={() => onChange(value + 5)} sx={segment}><AddRounded fontSize="small" /></IconButton>
  </Paper>;
}

function NodeDetail({ node, tab, setTab, onClose }: { node: ResearchNode; tab: string; setTab: (tab: string) => void; onClose: () => void }) {
  const items = [["overview", "概览"], ["papers", `论文 ${node.papers?.length ?? 0}`], ["progress", "进展"], ["relations", "关系"], ["ideas", "想法"]];
  return <Stack sx={{ minHeight: "100%" }}><Box sx={{ p: 3, pb: 2 }}><Stack direction="row" justifyContent="space-between"><Chip size="small" label={node.domain || "研究节点"} color="primary" variant="outlined" /><IconButton onClick={onClose}><CloseRounded /></IconButton></Stack><Typography variant="h1" sx={{ mt: 2 }}>{node.name}</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>{node.core_question}</Typography></Box><Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" scrollButtons={false} sx={{ px: 2, borderBottom: 1, borderColor: "divider" }}>{items.map(([value, label]) => <Tab value={value} label={label} key={value} />)}</Tabs><Box sx={{ p: 3, flex: 1 }}>{tab === "overview" && <Stack spacing={2}><InfoBlock title="研究内容" body={node.summary} /><InfoBlock title="已有进展" body={node.solved} /><InfoBlock title="待解决问题" body={node.open_questions} /></Stack>}{tab === "papers" && <Stack spacing={1}>{node.papers?.map((paper) => <Paper component={Link} to={`/paper/${paper.id}`} key={paper.id} variant="outlined" sx={{ p: 1.75, display: "flex", gap: 1.5, color: "inherit", textDecoration: "none" }}><DescriptionRounded color="primary" /><Box><Typography fontWeight={650}><PaperTitle title={paper.title} /></Typography><Typography variant="caption" color="text.secondary">{paper.first_author} · {formatDate(paper.published_at)}</Typography>{paper.reason && <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>{paper.reason}</Typography>}</Box></Paper>)}</Stack>}{tab === "progress" && <Stack spacing={2} divider={<Divider />}>{node.progress?.map((item) => <Box key={item.id}><Typography variant="caption" color="primary">{formatDate(item.happened_at || item.created_at)}</Typography><Typography variant="h3" sx={{ mt: .5 }}>{item.title}</Typography><Typography color="text.secondary" sx={{ mt: .5 }}>{item.content}</Typography></Box>)}</Stack>}{tab === "relations" && <Stack spacing={3}><Relation title="上游节点" nodes={node.parents} /><Relation title="下游节点" nodes={node.children} /></Stack>}{tab === "ideas" && <Stack spacing={1.5}>{node.ideas?.map((idea) => <Paper variant="outlined" sx={{ p: 1.75 }} key={idea.id}><Stack direction="row" spacing={1}><LightbulbRounded color="primary" /><Box><Chip size="small" label={idea.status} /><Typography sx={{ mt: 1 }}>{idea.content}</Typography></Box></Stack></Paper>)}{!node.ideas?.length && <Typography color="text.secondary">暂无已保存的研究想法。</Typography>}</Stack>}</Box><Box sx={{ p: 2.5, borderTop: 1, borderColor: "divider" }}><Button fullWidth variant="contained" startIcon={<PsychologyRounded />} onClick={() => window.dispatchEvent(new CustomEvent("frontier:open-agent", { detail: { context: `研究节点：${node.name}\n核心问题：${node.core_question}` } }))}>与研究助手讨论</Button></Box></Stack>;
}
function InfoBlock({ title, body }: { title: string; body?: string | null }) { return <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2" color="primary.dark">{title}</Typography><Typography color="text.secondary" sx={{ mt: .75, whiteSpace: "pre-wrap" }}>{body || "暂无内容。"}</Typography></Paper>; }
function Relation({ title, nodes = [] }: { title: string; nodes?: Array<{ id: string; name: string }> }) { return <Box><Typography variant="h3" sx={{ mb: 1 }}>{title}</Typography>{nodes.length ? <Stack>{nodes.map((node) => <MuiLink component={Link} key={node.id} to={`/graph/${node.id}`} underline="none" sx={{ py: 1, display: "flex", justifyContent: "space-between" }}>{node.name}<ChevronRightRounded fontSize="small" /></MuiLink>)}</Stack> : <Typography color="text.secondary">暂无节点</Typography>}</Box>; }
