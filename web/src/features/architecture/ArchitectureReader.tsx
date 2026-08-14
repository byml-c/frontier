import AccountTreeRounded from "@mui/icons-material/AccountTreeRounded";
import AutoFixHighRounded from "@mui/icons-material/AutoFixHighRounded";
import CenterFocusStrongRounded from "@mui/icons-material/CenterFocusStrongRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import RestartAltRounded from "@mui/icons-material/RestartAltRounded";
import { Box, Chip, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import {
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getBezierPath,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from "react";
import { ArchitectureNodeCard } from "./ArchitectureNodeCard";
import { ArchitectureMath, ArchitectureRichText } from "./ArchitectureText";
import { architectureCardSize } from "./architectureCardSize";
import { organizeModuleLayout } from "./architectureLayout";
import type {
  ArchitectureAsset,
  ArchitectureCommand,
  ArchitectureNode,
  ArchitectureReaderContext,
  ArchitectureScene,
} from "./types";
import { readArchitectureViewState, writeArchitectureViewState, type ArchitectureViewState } from "./viewState";

const GAP = 44;

type CanvasNodeData = {
  entity?: ArchitectureNode;
  title: string;
  subtitle?: string;
  keywords?: string[];
  expanded?: boolean;
  expandable?: boolean;
  dimmed?: boolean;
  highlighted?: boolean;
  assetUrl?: string;
  onToggle?: (id: string) => void;
};

type CanvasEdgeData = { label?: string; labelIsMath?: boolean; secondary?: string; highlighted?: boolean; dimmed?: boolean; raised?: boolean };

export type ArchitectureReaderHandle = {
  execute(commands: ArchitectureCommand[]): void;
  resetView(): void;
  getContext(): ArchitectureReaderContext;
};

type Props = {
  paperId: string;
  revision: number;
  scene: ArchitectureScene;
  resolveAsset: (asset: ArchitectureAsset) => string | undefined;
  onNodeOpen: (node: ArchitectureNode) => void;
  onContextChange?: (context: ArchitectureReaderContext) => void;
};

function anchors() {
  return <>{([Position.Left, Position.Right, Position.Top, Position.Bottom] as const).flatMap((position) => { const name = position.toLowerCase(); return [<Handle key={`target-${name}`} type="target" position={position} id={`target-${name}`} style={{ opacity: 0 }} />, <Handle key={`source-${name}`} type="source" position={position} id={`source-${name}`} style={{ opacity: 0 }} />]; })}</>;
}

function ModuleCard({ data }: NodeProps<Node<CanvasNodeData>>) {
  const entity = data.entity;
  if (!entity) return null;
  return <ArchitectureNodeCard node={entity} compact fill={Boolean(data.expanded && data.expandable)} highlighted={data.highlighted} dimmed={data.dimmed} reserveActionSpace={data.expandable} overlay={<>{anchors()}{data.expandable && <IconButton className="nodrag" size="small" aria-label={data.expanded ? "收起模块" : "展开模块"} onClick={(event) => { event.stopPropagation(); data.onToggle?.(entity.id); }} sx={{ position: "absolute", zIndex: 2, top: 3, right: 3, transform: data.expanded ? "rotate(180deg)" : "none", transition: "transform .18s" }}><ExpandMoreRounded fontSize="small" /></IconButton>}</>} />;
}

function DataCard({ data }: NodeProps<Node<CanvasNodeData>>) {
  if (!data.entity) return null;
  return <ArchitectureNodeCard node={data.entity} compact highlighted={data.highlighted} dimmed={data.dimmed} assetUrl={data.assetUrl} overlay={anchors()} />;
}

function GroupRegion({ data }: NodeProps<Node<CanvasNodeData>>) {
  return <Box sx={{ width: "100%", height: "100%", border: "1px dashed", borderColor: "rgba(91,105,125,.45)", bgcolor: "rgba(244,247,251,.42)", borderRadius: 1.5, pointerEvents: "none" }}><Typography variant="caption" sx={{ position: "absolute", top: 8, left: 12, color: "text.secondary", fontWeight: 700, letterSpacing: ".04em" }}>{data.title}</Typography></Box>;
}

function ArchitectureFlowEdge(props: EdgeProps<Edge<CanvasEdgeData>>) {
  const [path, labelX, labelY] = getBezierPath(props);
  const data = props.data;
  return <><BaseEdge path={path} markerEnd={props.markerEnd} style={{ stroke: data?.highlighted ? "#006FE6" : "#657184", strokeWidth: data?.highlighted ? 2.6 : 1.5, strokeDasharray: props.id.startsWith("conditioning:") ? "6 5" : undefined, opacity: data?.dimmed ? .18 : 1 }} />{(data?.label || data?.secondary) && <EdgeLabelRenderer><Box className="nodrag nopan" sx={{ position: "absolute", zIndex: data?.raised ? 4 : 1, transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`, minWidth: "max-content", maxWidth: 240, px: .8, py: .4, bgcolor: "rgba(255,255,255,.97)", border: 1, borderColor: "divider", boxShadow: "0 2px 8px rgba(15,23,42,.08)", borderRadius: .75, pointerEvents: "all", textAlign: "center" }}>{data.label && (data.labelIsMath ? <ArchitectureMath value={data.label} sx={{ fontSize: ".76rem", fontWeight: 650 }} /> : <ArchitectureRichText content={data.label} sx={{ fontSize: ".74rem", fontWeight: 650 }} />)}{data.secondary && <ArchitectureMath value={data.secondary} sx={{ display: "flex", justifyContent: "center", color: "text.secondary", fontSize: ".7rem", lineHeight: 1.2, whiteSpace: "nowrap" }} />}</Box></EdgeLabelRenderer>}</>;
}

const nodeTypes = { module: ModuleCard, data: DataCard, groupRegion: GroupRegion };
const edgeTypes = { architecture: ArchitectureFlowEdge };

function nearestVisible(id: string, visible: Set<string>, byId: Map<string, ArchitectureNode>): string | undefined {
  let current = byId.get(id);
  while (current) {
    if (visible.has(current.id)) return current.id;
    current = current.parentModuleId ? byId.get(current.parentModuleId) : undefined;
  }
  return undefined;
}

function nodeSize(node: ArchitectureNode, expanded = false) {
  const content = architectureCardSize(node);
  if (!expanded || !node.size) return content;
  return { width: Math.max(content.width, node.size.width), height: Math.max(content.height, node.size.height) };
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

function ArchitectureReaderInner({ paperId, revision, scene, resolveAsset, onNodeOpen, onContextChange }: Props, ref: React.ForwardedRef<ArchitectureReaderHandle>) {
  const flow = useReactFlow();
  const [view, setView] = useState<ArchitectureViewState>(() => readArchitectureViewState(paperId, scene.layoutEpoch));
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());
  const [dimExcept, setDimExcept] = useState<Set<string> | null>(null);
  const [shownTensors, setShownTensors] = useState<Set<string>>(new Set());
  const byId = useMemo(() => new Map(scene.nodes.map((node) => [node.id, node])), [scene.nodes]);
  const children = useMemo(() => {
    const values = new Map<string, ArchitectureNode[]>();
    scene.nodes.forEach((node) => { if (node.parentModuleId) values.set(node.parentModuleId, [...(values.get(node.parentModuleId) ?? []), node]); });
    return values;
  }, [scene.nodes]);
  const expanded = useMemo(() => new Set(view.expandedNodeIds), [view.expandedNodeIds]);

  useEffect(() => { const next = readArchitectureViewState(paperId, scene.layoutEpoch); setView(next); }, [paperId, scene.layoutEpoch]);
  useEffect(() => { writeArchitectureViewState(paperId, view); }, [paperId, view]);
  useEffect(() => { onContextChange?.({ revision, selectedNodeIds: selectedIds, expandedNodeIds: view.expandedNodeIds }); }, [onContextChange, revision, selectedIds, view.expandedNodeIds]);

  function toggleExpanded(id: string) {
    setView((current) => ({ ...current, expandedNodeIds: current.expandedNodeIds.includes(id) ? current.expandedNodeIds.filter((value) => value !== id) : [...current.expandedNodeIds, id] }));
  }

  function organizeExpandedModules() {
    const expandedModules = scene.nodes.filter((node) => node.type === "module" && expanded.has(node.id) && (children.get(node.id)?.length ?? 0) > 0);
    if (!expandedModules.length) return;
    setView((current) => {
      const positionOverrides = { ...current.positionOverrides };
      const sizeOverrides = { ...current.sizeOverrides };
      expandedModules.forEach((parent) => {
        const childNodes = children.get(parent.id) ?? [];
        const childIds = new Set(childNodes.map((node) => node.id));
        const internalEdges = scene.edges.filter((edge) => childIds.has(edge.source) && childIds.has(edge.target));
        const layout = organizeModuleLayout(parent, childNodes, internalEdges);
        const parentPosition = flow.getNode(parent.id)?.position ?? positionOverrides[parent.id] ?? parent.position;
        Object.entries(layout.positions).forEach(([id, position]) => {
          positionOverrides[id] = { x: parentPosition.x + position.x, y: parentPosition.y + position.y };
        });
        sizeOverrides[parent.id] = layout.size;
      });
      return { ...current, positionOverrides, sizeOverrides };
    });
  }

  const derived = useMemo(() => {
    const roots = scene.nodes.filter((node) => !node.parentModuleId);
    const rootPositions = new Map(roots.map((node) => [node.id, { ...(view.positionOverrides[node.id] ?? node.position) }]));
    const expandedSize = (node: ArchitectureNode) => {
      const base = expanded.has(node.id) && view.sizeOverrides[node.id] ? view.sizeOverrides[node.id] : nodeSize(node, expanded.has(node.id));
      if (!expanded.has(node.id)) return base;
      const descendants = children.get(node.id) ?? [];
      return descendants.reduce((size, child) => {
        const childDimensions = nodeSize(child);
        return { width: Math.max(size.width, child.position.x + childDimensions.width + 24), height: Math.max(size.height, child.position.y + childDimensions.height + 24) };
      }, { ...base });
    };
    for (let pass = 0; pass < roots.length * 2; pass += 1) {
      let moved = false;
      roots.forEach((first, index) => roots.slice(index + 1).forEach((second) => {
        const a = { ...rootPositions.get(first.id)!, ...expandedSize(first) };
        const bPosition = rootPositions.get(second.id)!;
        const b = { ...bPosition, ...expandedSize(second) };
        if (overlaps(a, b)) { rootPositions.set(second.id, { x: a.x + a.width + GAP, y: b.y }); moved = true; }
      }));
      if (!moved) break;
    }
    const absolute = new Map<string, { x: number; y: number }>();
    const locate = (node: ArchitectureNode): { x: number; y: number } => {
      const existing = absolute.get(node.id); if (existing) return existing;
      const override = view.positionOverrides[node.id];
      if (override) { absolute.set(node.id, override); return override; }
      if (!node.parentModuleId) { const value = rootPositions.get(node.id) ?? node.position; absolute.set(node.id, value); return value; }
      const parent = byId.get(node.parentModuleId); const parentPosition = parent ? locate(parent) : { x: 0, y: 0 };
      const value = { x: parentPosition.x + node.position.x, y: parentPosition.y + node.position.y }; absolute.set(node.id, value); return value;
    };
    const visible = new Set<string>();
    const isVisible = (node: ArchitectureNode): boolean => {
      if (!node.parentModuleId) return true;
      const parent = byId.get(node.parentModuleId);
      return Boolean(parent && expanded.has(parent.id) && isVisible(parent));
    };
    scene.nodes.forEach((node) => { if (isVisible(node)) visible.add(node.id); });
    const assetById = new Map(scene.assets.map((asset) => [asset.id, asset]));
    const flowNodes: Node<CanvasNodeData>[] = [
      ...scene.groups.map((group) => ({ id: `group:${group.id}`, type: "groupRegion", position: group.position, draggable: false, selectable: false, zIndex: -10, style: { width: group.size.width, height: group.size.height }, data: { title: group.title } })),
      ...scene.nodes.filter((node) => visible.has(node.id)).map((node) => {
        const size = expandedSize(node);
        const isExpanded = expanded.has(node.id) && Boolean(children.get(node.id)?.length);
        const primary = node.type === "data" ? node.examples?.find((example) => example.id === node.primaryExampleId) : undefined;
        const asset = primary?.assetId ? assetById.get(primary.assetId) : undefined;
        return { id: node.id, type: node.type, position: locate(node), zIndex: node.parentModuleId ? 5 : 1, style: { width: size.width, ...(isExpanded ? { height: size.height } : {}) }, data: { entity: node, title: node.title, subtitle: node.type === "module" ? node.architecture : node.symbol, keywords: node.type === "module" ? node.keywords : node.preprocessing, expanded: expanded.has(node.id), expandable: Boolean(children.get(node.id)?.length), dimmed: Boolean(dimExcept && !dimExcept.has(node.id)), highlighted: highlighted.has(node.id), assetUrl: asset ? resolveAsset(asset) : undefined, onToggle: toggleExpanded } };
      }),
    ];
    const tensors = new Map(scene.tensors.map((tensor) => [tensor.id, tensor]));
    const flowEdges: Edge<CanvasEdgeData>[] = scene.edges.flatMap((edge) => {
      const source = nearestVisible(edge.source, visible, byId); const target = nearestVisible(edge.target, visible, byId);
      if (!source || !target || source === target) return [];
      const tensor = edge.tensorId ? tensors.get(edge.tensorId) : undefined;
      const edgeHighlighted = highlighted.has(edge.id) || highlighted.has(source) && highlighted.has(target) || Boolean(edge.tensorId && shownTensors.has(edge.tensorId));
      const expandedFlow = Boolean(byId.get(source)?.parentModuleId || byId.get(target)?.parentModuleId);
      return [{ id: `${edge.kind === "conditioning" ? "conditioning:" : "flow:"}${edge.id}`, source, target, sourceHandle: `source-${edge.sourceAnchor ?? "right"}`, targetHandle: `target-${edge.targetAnchor ?? "left"}`, type: "architecture", zIndex: expandedFlow ? 4 : 0, markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: edgeHighlighted ? "#006FE6" : "#657184" }, data: { label: tensor?.symbol || tensor?.name || edge.label, labelIsMath: Boolean(tensor?.symbol), secondary: tensor?.shape, highlighted: edgeHighlighted, dimmed: Boolean(dimExcept && (!dimExcept.has(source) || !dimExcept.has(target))), raised: expandedFlow } }];
    });
    return { nodes: flowNodes, edges: flowEdges };
  }, [byId, children, dimExcept, expanded, highlighted, resolveAsset, scene, shownTensors, view.positionOverrides, view.sizeOverrides]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<CanvasNodeData>>(derived.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<CanvasEdgeData>>(derived.edges);
  useEffect(() => { setNodes(derived.nodes); setEdges(derived.edges); }, [derived, setEdges, setNodes]);

  function clearTransient() { setHighlighted(new Set()); setDimExcept(null); setShownTensors(new Set()); }
  useImperativeHandle(ref, () => ({
    execute(commands) {
      let focusTarget: string | undefined;
      setView((current) => {
        let expandedNodeIds = [...current.expandedNodeIds];
        for (const command of commands) {
          if (command.type === "expand") expandedNodeIds = [...new Set([...expandedNodeIds, command.target])];
          if (command.type === "collapse") expandedNodeIds = expandedNodeIds.filter((id) => id !== command.target);
          if (command.type === "focus") {
            focusTarget = command.target;
            let target = byId.get(command.target);
            while (target?.parentModuleId) {
              expandedNodeIds = [...new Set([...expandedNodeIds, target.parentModuleId])];
              target = byId.get(target.parentModuleId);
            }
          }
        }
        return { ...current, expandedNodeIds };
      });
      commands.forEach((command) => {
        if (command.type === "highlight" || command.type === "highlight-path") setHighlighted(new Set(command.targets));
        if (command.type === "dim-others") setDimExcept(new Set(command.except));
        if (command.type === "show-tensor") setShownTensors(new Set([command.tensorId]));
        if (command.type === "clear-transient") clearTransient();
      });
      if (focusTarget) {
        const target = focusTarget;
        window.setTimeout(() => flow.fitView({ nodes: [{ id: target }], duration: 450, padding: .7 }), 100);
      }
    },
    resetView() { const next = readArchitectureViewState(`${paperId}:reset`, scene.layoutEpoch); setView(next); window.requestAnimationFrame(() => flow.fitView({ duration: 300, padding: .18 })); },
    getContext() { return { revision, selectedNodeIds: selectedIds, expandedNodeIds: view.expandedNodeIds }; },
  }), [byId, flow, paperId, revision, scene.layoutEpoch, selectedIds, view.expandedNodeIds]);

  return <Box sx={{ width: "100%", height: "100%", position: "relative", bgcolor: "#FAFBFC" }}>
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={(_, node) => { const entity = byId.get(node.id); if (entity) { setSelectedIds([entity.id]); onNodeOpen(entity); } }} onPaneClick={() => setSelectedIds([])} onNodeDragStop={(_, node) => { if (node.id.startsWith("group:")) return; setView((current) => ({ ...current, positionOverrides: { ...current.positionOverrides, [node.id]: node.position } })); }} onMoveEnd={(_, viewport: Viewport) => setView((current) => ({ ...current, viewport }))} defaultViewport={view.viewport} minZoom={.25} maxZoom={2.5} fitViewOptions={{ padding: .18 }} proOptions={{ hideAttribution: true }}>
      <Background color="#DCE1E8" gap={24} size={1} />
    </ReactFlow>
    <Stack direction="row" spacing={.5} sx={{ position: "absolute", top: 12, right: 12, p: .5, bgcolor: "rgba(255,255,255,.92)", border: 1, borderColor: "divider", borderRadius: 1, zIndex: 8 }}><Tooltip title="整理已展开模块"><span><IconButton size="small" disabled={!view.expandedNodeIds.some((id) => (children.get(id)?.length ?? 0) > 0)} onClick={organizeExpandedModules}><AutoFixHighRounded fontSize="small" /></IconButton></span></Tooltip><Tooltip title="适合画布"><IconButton size="small" onClick={() => flow.fitView({ duration: 300, padding: .18 })}><CenterFocusStrongRounded fontSize="small" /></IconButton></Tooltip><Tooltip title="重置本地布局"><IconButton size="small" onClick={() => { const next = readArchitectureViewState(`${paperId}:reset`, scene.layoutEpoch); setView(next); flow.fitView({ duration: 300, padding: .18 }); }}><RestartAltRounded fontSize="small" /></IconButton></Tooltip>{(highlighted.size > 0 || dimExcept || shownTensors.size > 0) && <Chip size="small" icon={<AccountTreeRounded />} label="清除讲解" onDelete={clearTransient} />}</Stack>
  </Box>;
}

const ForwardedInner = forwardRef(ArchitectureReaderInner);

export const ArchitectureReader = forwardRef<ArchitectureReaderHandle, Props>(function ArchitectureReader(props, ref) {
  return <ReactFlowProvider><ForwardedInner {...props} ref={ref} /></ReactFlowProvider>;
});
