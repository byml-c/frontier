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
  applyNodeChanges,
  getSmoothStepPath,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { ArchitectureNodeCard } from "./ArchitectureNodeCard";
import { ArchitectureMath, ArchitectureRichText } from "./ArchitectureText";
import { architectureCardSize } from "./architectureCardSize";
import { bundleArchitectureEdges, parallelArchitectureEdgeOffsets, reciprocalArchitectureEdgeAnchors } from "./architectureEdges";
import { architectureFocusNeighborhood } from "./architectureFocus";
import { architectureGroupBounds } from "./architectureGroups";
import { organizeArchitectureLayout } from "./architectureLayout";
import type {
  ArchitectureAsset,
  ArchitectureCommand,
  ArchitectureNode,
  ArchitectureReaderContext,
  ArchitectureScene,
} from "./types";
import { readArchitectureViewState, writeArchitectureViewState, type ArchitectureViewState } from "./viewState";

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
  onRequestExpand?: (node: ArchitectureNode) => void;
};

type CanvasEdgeLabel = { primary: string; primaryIsMath?: boolean; secondary?: string };
type CanvasEdgeData = { labels: CanvasEdgeLabel[]; highlighted?: boolean; dimmed?: boolean; raised?: boolean; routeOffset?: number; parallelOffset?: number; dashed?: boolean };

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
  onRequestExpand?: (node: ArchitectureNode) => void;
  onContextChange?: (context: ArchitectureReaderContext) => void;
};

function anchors() {
  return <>{([Position.Left, Position.Right, Position.Top, Position.Bottom] as const).flatMap((position) => { const name = position.toLowerCase(); return [<Handle key={`target-${name}`} type="target" position={position} id={`target-${name}`} style={{ opacity: 0 }} />, <Handle key={`source-${name}`} type="source" position={position} id={`source-${name}`} style={{ opacity: 0 }} />]; })}</>;
}

function ModuleCard({ data }: NodeProps<Node<CanvasNodeData>>) {
  const entity = data.entity;
  if (!entity) return null;
  return <ArchitectureNodeCard node={entity} compact fill={Boolean(data.expanded && data.expandable)} highlighted={data.highlighted} dimmed={data.dimmed} reserveActionSpace interactiveTitle overlay={<>{anchors()}<Tooltip title={data.expandable ? (data.expanded ? "收起模块" : "展开模块") : "请求 Agent 展开"}><IconButton className="nodrag" size="small" aria-label={data.expandable ? (data.expanded ? "收起模块" : "展开模块") : "请求 Agent 展开节点"} onClick={(event) => { event.stopPropagation(); if (data.expandable) data.onToggle?.(entity.id); else data.onRequestExpand?.(entity); }} sx={{ position: "absolute", zIndex: 2, top: 3, right: 3, transform: data.expandable && data.expanded ? "rotate(180deg)" : "none", transition: "transform .18s" }}><ExpandMoreRounded fontSize="small" /></IconButton></Tooltip></>} />;
}

function DataCard({ data }: NodeProps<Node<CanvasNodeData>>) {
  if (!data.entity) return null;
  const entity = data.entity;
  return <ArchitectureNodeCard node={entity} compact highlighted={data.highlighted} dimmed={data.dimmed} assetUrl={data.assetUrl} reserveActionSpace interactiveTitle overlay={<>{anchors()}<Tooltip title="请求 Agent 展开"><IconButton className="nodrag" size="small" aria-label="请求 Agent 展开节点" onClick={(event) => { event.stopPropagation(); data.onRequestExpand?.(entity); }} sx={{ position: "absolute", zIndex: 2, top: 3, right: 3 }}><ExpandMoreRounded fontSize="small" /></IconButton></Tooltip></>} />;
}

function GroupRegion({ data }: NodeProps<Node<CanvasNodeData>>) {
  return <Box sx={{ width: "100%", height: "100%", border: "1px dashed", borderColor: "rgba(91,105,125,.45)", bgcolor: "rgba(244,247,251,.42)", borderRadius: 1.5, pointerEvents: "none" }}><Typography variant="caption" sx={{ position: "absolute", top: 8, left: 12, color: "text.secondary", fontWeight: 700, letterSpacing: ".04em" }}>{data.title}</Typography></Box>;
}

function ArchitectureFlowEdge(props: EdgeProps<Edge<CanvasEdgeData>>) {
  const data = props.data;
  const smoothPath = getSmoothStepPath({ ...props, borderRadius: 12, offset: data?.routeOffset ?? 28 });
  let [path, labelX, labelY] = smoothPath;
  if (data?.parallelOffset) {
    const dx = props.targetX - props.sourceX;
    const dy = props.targetY - props.sourceY;
    const length = Math.max(1, Math.hypot(dx, dy));
    const normalX = -dy / length;
    const normalY = dx / length;
    const midpointX = (props.sourceX + props.targetX) / 2;
    const midpointY = (props.sourceY + props.targetY) / 2;
    const controlX = midpointX + normalX * data.parallelOffset * 2;
    const controlY = midpointY + normalY * data.parallelOffset * 2;
    path = `M ${props.sourceX},${props.sourceY} Q ${controlX},${controlY} ${props.targetX},${props.targetY}`;
    labelX = midpointX + normalX * data.parallelOffset;
    labelY = midpointY + normalY * data.parallelOffset;
  }
  const opacity = data?.dimmed ? .1 : 1;
  return <><BaseEdge path={path} markerEnd={props.markerEnd} style={{ stroke: data?.highlighted ? "#006FE6" : "#657184", strokeWidth: data?.highlighted ? 2.6 : 1.5, strokeDasharray: data?.dashed ? "6 5" : undefined, opacity, transition: "opacity .16s, stroke .16s, stroke-width .16s" }} />{Boolean(data?.labels.length) && <EdgeLabelRenderer><Box className="nodrag nopan" sx={{ position: "absolute", zIndex: 7, transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`, minWidth: "max-content", maxWidth: 240, px: .8, py: .45, bgcolor: "rgba(255,255,255,.98)", border: 1, borderColor: data?.highlighted ? "primary.light" : "divider", boxShadow: "0 2px 9px rgba(15,23,42,.1)", borderRadius: .75, pointerEvents: "none", textAlign: "center", opacity, transition: "opacity .16s" }}>{data?.labels.map((label, index) => <Box key={`${label.primary}:${label.secondary ?? ""}`} sx={{ mt: index ? .2 : 0 }}>{label.primaryIsMath ? <ArchitectureMath value={label.primary} sx={{ fontSize: ".76rem", fontWeight: 650 }} /> : <ArchitectureRichText content={label.primary} sx={{ fontSize: ".74rem", fontWeight: 650 }} />}{label.secondary && <ArchitectureMath value={label.secondary} sx={{ display: "flex", justifyContent: "center", color: "text.secondary", fontSize: ".7rem", lineHeight: 1.2, whiteSpace: "nowrap" }} />}</Box>)}</Box></EdgeLabelRenderer>}</>;
}

function EdgeLegendLine({ dashed = false }: { dashed?: boolean }) {
  return <Box sx={{ width: 34, height: 10, position: "relative", flex: "0 0 auto", "&::before": { content: '""', position: "absolute", left: 0, right: 5, top: 4, borderTop: "2px solid #657184", borderTopStyle: dashed ? "dashed" : "solid" }, "&::after": { content: '""', position: "absolute", right: 0, top: 1, width: 0, height: 0, borderTop: "4px solid transparent", borderBottom: "4px solid transparent", borderLeft: "6px solid #657184" } }} />;
}

function ArchitectureEdgeLegend() {
  return <Stack aria-label="架构连线图例" spacing={.5} sx={{ position: "absolute", right: 12, bottom: 12, zIndex: 8, p: .75, bgcolor: "rgba(255,255,255,.94)", border: 1, borderColor: "divider", borderRadius: 1, boxShadow: "0 2px 8px rgba(15,23,42,.06)" }}><Stack direction="row" alignItems="center" spacing={.75}><EdgeLegendLine /><Typography variant="caption" color="text.secondary">主数据流</Typography></Stack><Stack direction="row" alignItems="center" spacing={.75}><EdgeLegendLine dashed /><Typography variant="caption" color="text.secondary">条件 / 辅助输入</Typography></Stack></Stack>;
}

const nodeTypes = { module: ModuleCard, data: DataCard, groupRegion: GroupRegion };
const edgeTypes = { architecture: ArchitectureFlowEdge };
const ROOT_GAP = 44;

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

function adaptiveEdgeAnchors(sourcePosition: { x: number; y: number }, sourceSize: { width: number; height: number }, targetPosition: { x: number; y: number }, targetSize: { width: number; height: number }) {
  const dx = targetPosition.x + targetSize.width / 2 - sourcePosition.x - sourceSize.width / 2;
  const dy = targetPosition.y + targetSize.height / 2 - sourcePosition.y - sourceSize.height / 2;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? { source: "right", target: "left" } : { source: "left", target: "right" };
  return dy >= 0 ? { source: "bottom", target: "top" } : { source: "top", target: "bottom" };
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

function ArchitectureReaderInner({ paperId, revision, scene, resolveAsset, onNodeOpen, onRequestExpand, onContextChange }: Props, ref: React.ForwardedRef<ArchitectureReaderHandle>) {
  const flow = useReactFlow();
  const [view, setView] = useState<ArchitectureViewState>(() => readArchitectureViewState(paperId, scene.layoutEpoch));
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());
  const [dimExcept, setDimExcept] = useState<Set<string> | null>(null);
  const [shownTensors, setShownTensors] = useState<Set<string>>(new Set());
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const nodeClickTimer = useRef<number | undefined>(undefined);
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
  useEffect(() => () => window.clearTimeout(nodeClickTimer.current), []);

  function toggleExpanded(id: string) {
    setView((current) => ({ ...current, expandedNodeIds: current.expandedNodeIds.includes(id) ? current.expandedNodeIds.filter((value) => value !== id) : [...current.expandedNodeIds, id] }));
  }

  function organizeAllNodes() {
    const tensors = new Map(scene.tensors.map((tensor) => [tensor.id, tensor]));
    const edgeLabels = Object.fromEntries(scene.edges.map((edge) => {
      const tensor = edge.tensorId ? tensors.get(edge.tensorId) : undefined;
      return [edge.id, [tensor?.symbol || tensor?.name || edge.label, tensor?.shape].filter(Boolean).join(" ")];
    }));
    const layout = organizeArchitectureLayout(scene.nodes, scene.edges, expanded, edgeLabels);
    setView((current) => {
      return { ...current, positionOverrides: layout.positions, sizeOverrides: layout.sizeOverrides };
    });
    window.setTimeout(() => flow.fitView({ duration: 420, padding: .16 }), 50);
  }

  const derived = useMemo(() => {
    const roots = scene.nodes.filter((node) => !node.parentModuleId);
    const rootPositions = new Map(roots.map((node) => [node.id, { ...(view.positionOverrides[node.id] ?? node.position) }]));
    const expandedSize = (node: ArchitectureNode) => {
      if (expanded.has(node.id) && view.sizeOverrides[node.id]) return view.sizeOverrides[node.id];
      const base = nodeSize(node, expanded.has(node.id));
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
        const firstBounds = { ...rootPositions.get(first.id)!, ...expandedSize(first) };
        const secondPosition = rootPositions.get(second.id)!;
        if (overlaps(firstBounds, { ...secondPosition, ...expandedSize(second) })) {
          rootPositions.set(second.id, { x: firstBounds.x + firstBounds.width + ROOT_GAP, y: secondPosition.y });
          moved = true;
        }
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
    const focusNeighborhood = architectureFocusNeighborhood(scene.nodes, scene.edges, visible, focusedNodeId);
    const assetById = new Map(scene.assets.map((asset) => [asset.id, asset]));
    const nodePositions = Object.fromEntries(scene.nodes.filter((node) => visible.has(node.id)).map((node) => [node.id, locate(node)]));
    const nodeSizes = Object.fromEntries(scene.nodes.filter((node) => visible.has(node.id)).map((node) => [node.id, expandedSize(node)]));
    const flowNodes: Node<CanvasNodeData>[] = [
      ...scene.groups.map((group) => { const bounds = architectureGroupBounds(group, scene.nodes, visible, nodePositions, nodeSizes); return { id: `group:${group.id}`, type: "groupRegion", position: bounds.position, draggable: false, selectable: false, zIndex: -10, style: { width: bounds.size.width, height: bounds.size.height }, data: { title: group.title } }; }),
      ...scene.nodes.filter((node) => visible.has(node.id)).map((node) => {
        const size = expandedSize(node);
        const isExpanded = expanded.has(node.id) && Boolean(children.get(node.id)?.length);
        const primary = node.type === "data" ? node.examples?.find((example) => example.id === node.primaryExampleId) : undefined;
        const asset = primary?.assetId ? assetById.get(primary.assetId) : undefined;
        return { id: node.id, type: node.type, position: locate(node), dragHandle: ".architecture-node-title", zIndex: node.parentModuleId ? 5 : 1, style: { width: size.width, ...(isExpanded ? { height: size.height } : {}) }, data: { entity: node, title: node.title, subtitle: node.type === "module" ? node.architecture : node.symbol, keywords: node.type === "module" ? node.keywords : node.preprocessing, expanded: expanded.has(node.id), expandable: Boolean(children.get(node.id)?.length), dimmed: Boolean(dimExcept && !dimExcept.has(node.id) || focusNeighborhood && !focusNeighborhood.has(node.id)), highlighted: highlighted.has(node.id) || focusedNodeId === node.id, assetUrl: asset ? resolveAsset(asset) : undefined, onToggle: toggleExpanded, onRequestExpand } };
      }),
    ];
    const tensors = new Map(scene.tensors.map((tensor) => [tensor.id, tensor]));
    const visibleEdgeItems = scene.edges.flatMap((edge) => {
      const source = nearestVisible(edge.source, visible, byId); const target = nearestVisible(edge.target, visible, byId);
      if (!source || !target || source === target) return [];
      return [{ edge, source, target }];
    });
    const visibleEdges = bundleArchitectureEdges(visibleEdgeItems);
    const outgoing = new Map<string, typeof visibleEdges>();
    visibleEdges.forEach((item) => {
      outgoing.set(item.source, [...(outgoing.get(item.source) ?? []), item]);
    });
    const visibleRelationKeys = new Set(visibleEdges.map((item) => `${item.kind}\u0000${item.source}\u0000${item.target}`));
    const parallelOffsets = parallelArchitectureEdgeOffsets(visibleEdges);
    const flowEdges: Edge<CanvasEdgeData>[] = visibleEdges.map((bundle) => {
      const { id, kind, edges: bundledEdges, source, target } = bundle;
      const edgeHighlighted = bundledEdges.some((edge) => highlighted.has(edge.id) || Boolean(edge.tensorId && shownTensors.has(edge.tensorId))) || highlighted.has(source) && highlighted.has(target);
      const expandedFlow = Boolean(byId.get(source)?.parentModuleId || byId.get(target)?.parentModuleId);
      const sourcePeers = outgoing.get(source) ?? [];
      const sourceIndex = sourcePeers.findIndex((item) => item.id === id);
      const focusDimmed = Boolean(focusNeighborhood && (!focusNeighborhood.has(source) || !focusNeighborhood.has(target)));
      const sourceNode = byId.get(source)!;
      const targetNode = byId.get(target)!;
      const sourcePosition = locate(sourceNode); const sourceSize = expandedSize(sourceNode); const targetPosition = locate(targetNode); const targetSize = expandedSize(targetNode);
      const reciprocal = visibleRelationKeys.has(`${kind}\u0000${target}\u0000${source}`);
      const storedSourceAnchor = bundledEdges.find((edge) => edge.sourceAnchor)?.sourceAnchor;
      const storedTargetAnchor = bundledEdges.find((edge) => edge.targetAnchor)?.targetAnchor;
      const anchors = reciprocal ? reciprocalArchitectureEdgeAnchors({ x: sourcePosition.x + sourceSize.width / 2, y: sourcePosition.y + sourceSize.height / 2 }, { x: targetPosition.x + targetSize.width / 2, y: targetPosition.y + targetSize.height / 2 }) : storedSourceAnchor && storedTargetAnchor ? { source: storedSourceAnchor, target: storedTargetAnchor } : adaptiveEdgeAnchors(sourcePosition, sourceSize, targetPosition, targetSize);
      const labels: CanvasEdgeLabel[] = [];
      const labelKeys = new Set<string>();
      const appendLabel = (label: CanvasEdgeLabel) => { const key = `${label.primary}\u0000${label.secondary ?? ""}`; if (!labelKeys.has(key)) { labelKeys.add(key); labels.push(label); } };
      bundledEdges.forEach((edge) => { if (edge.label?.trim()) appendLabel({ primary: edge.label.trim() }); });
      bundledEdges.forEach((edge) => { const tensor = edge.tensorId ? tensors.get(edge.tensorId) : undefined; const primary = tensor?.symbol || tensor?.name; if (primary) appendLabel({ primary, primaryIsMath: Boolean(tensor?.symbol), secondary: tensor?.shape }); });
      return { id: `${kind === "conditioning" ? "conditioning:" : "flow:"}${id}`, source, target, sourceHandle: `source-${anchors.source}`, targetHandle: `target-${anchors.target}`, type: "architecture", zIndex: expandedFlow ? 4 : 0, markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: edgeHighlighted ? "#006FE6" : "#657184" }, data: { labels, highlighted: edgeHighlighted, dimmed: Boolean(dimExcept && (!dimExcept.has(source) || !dimExcept.has(target)) || focusDimmed), raised: expandedFlow, routeOffset: 26 + sourceIndex % 4 * 10, parallelOffset: reciprocal ? undefined : parallelOffsets.get(id), dashed: kind === "conditioning" } };
    });
    return { nodes: flowNodes, edges: flowEdges };
  }, [byId, children, dimExcept, expanded, focusedNodeId, highlighted, onRequestExpand, resolveAsset, scene, shownTensors, view.positionOverrides, view.sizeOverrides]);

  const [nodes, setNodes] = useNodesState<Node<CanvasNodeData>>(derived.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<CanvasEdgeData>>(derived.edges);
  useEffect(() => { setNodes(derived.nodes); setEdges(derived.edges); }, [derived, setEdges, setNodes]);

  function handleNodesChange(changes: NodeChange<Node<CanvasNodeData>>[]) {
    setNodes((current) => {
      const next = applyNodeChanges(changes, current);
      const entityNodes = next.filter((node) => node.data.entity);
      const visible = new Set(entityNodes.map((node) => node.id));
      const positions = Object.fromEntries(entityNodes.map((node) => [node.id, node.position]));
      const sizes = Object.fromEntries(entityNodes.map((node) => {
        const fallback = architectureCardSize(node.data.entity!);
        return [node.id, {
          width: typeof node.style?.width === "number" ? node.style.width : node.measured?.width ?? fallback.width,
          height: typeof node.style?.height === "number" ? node.style.height : node.measured?.height ?? fallback.height,
        }];
      }));
      return next.map((node) => {
        if (!node.id.startsWith("group:")) return node;
        const group = scene.groups.find((candidate) => `group:${candidate.id}` === node.id);
        if (!group) return node;
        const bounds = architectureGroupBounds(group, scene.nodes, visible, positions, sizes);
        return { ...node, position: bounds.position, style: { ...node.style, width: bounds.size.width, height: bounds.size.height } };
      });
    });
  }

  function clearTransient() { setHighlighted(new Set()); setDimExcept(null); setShownTensors(new Set()); }
  function cancelPendingNodeClick() { window.clearTimeout(nodeClickTimer.current); nodeClickTimer.current = undefined; }
  function handleNodeClick(_: React.MouseEvent, node: Node<CanvasNodeData>) {
    cancelPendingNodeClick();
    const entity = byId.get(node.id);
    if (!entity) { setSelectedIds([]); setFocusedNodeId(null); return; }
    nodeClickTimer.current = window.setTimeout(() => { setSelectedIds([entity.id]); setFocusedNodeId(entity.id); nodeClickTimer.current = undefined; }, 320);
  }
  function handleNodeDoubleClick(event: React.MouseEvent, node: Node<CanvasNodeData>) {
    if (!(event.target as Element).closest(".architecture-node-title")) return;
    event.stopPropagation();
    cancelPendingNodeClick();
    const entity = byId.get(node.id);
    if (entity) { setSelectedIds([entity.id]); setFocusedNodeId(entity.id); onNodeOpen(entity); }
    else { setSelectedIds([]); setFocusedNodeId(null); }
  }
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
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={handleNodesChange} onEdgesChange={onEdgesChange} zoomOnDoubleClick={false} nodeDragThreshold={8} nodeClickDistance={8} onNodeClick={handleNodeClick} onNodeDoubleClick={handleNodeDoubleClick} onPaneClick={() => { cancelPendingNodeClick(); setSelectedIds([]); setFocusedNodeId(null); }} onNodeDragStop={(_, node) => { if (node.id.startsWith("group:")) return; setView((current) => ({ ...current, positionOverrides: { ...current.positionOverrides, [node.id]: node.position } })); }} onMoveEnd={(_, viewport: Viewport) => setView((current) => ({ ...current, viewport }))} defaultViewport={view.viewport} minZoom={.25} maxZoom={2.5} fitViewOptions={{ padding: .18 }} proOptions={{ hideAttribution: true }}>
      <Background color="#DCE1E8" gap={24} size={1} />
    </ReactFlow>
    <Stack direction="row" alignItems="center" spacing={.5} sx={{ position: "absolute", top: 12, right: 12, p: .5, bgcolor: "rgba(255,255,255,.92)", border: 1, borderColor: "divider", borderRadius: 1, zIndex: 8 }}><Tooltip title="全局优化排版"><span><IconButton size="small" disabled={scene.nodes.length < 2} onClick={organizeAllNodes}><AutoFixHighRounded fontSize="small" /></IconButton></span></Tooltip><Tooltip title="适合画布"><IconButton size="small" onClick={() => flow.fitView({ duration: 300, padding: .18 })}><CenterFocusStrongRounded fontSize="small" /></IconButton></Tooltip><Tooltip title="重置本地布局"><IconButton size="small" onClick={() => { const next = readArchitectureViewState(`${paperId}:reset`, scene.layoutEpoch); setView(next); flow.fitView({ duration: 300, padding: .18 }); }}><RestartAltRounded fontSize="small" /></IconButton></Tooltip>{(highlighted.size > 0 || dimExcept || shownTensors.size > 0) && <Chip size="small" icon={<AccountTreeRounded />} label="清除讲解" onDelete={clearTransient} />}</Stack>
    <ArchitectureEdgeLegend />
  </Box>;
}

const ForwardedInner = forwardRef(ArchitectureReaderInner);

export const ArchitectureReader = forwardRef<ArchitectureReaderHandle, Props>(function ArchitectureReader(props, ref) {
  return <ReactFlowProvider><ForwardedInner {...props} ref={ref} /></ReactFlowProvider>;
});
