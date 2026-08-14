export type XYPosition = { x: number; y: number };
export type ArchitectureSize = { width: number; height: number };

export type ArchitectureSource = {
  id: string;
  kind: "paper-page" | "paper-figure" | "code" | "web";
  pageNumber?: number;
  blockId?: string;
  url?: string;
  label?: string;
};

export type ArchitectureAsset = {
  id: string;
  type: "image";
  src: string;
  caption?: string;
};

export type DataExample = {
  id: string;
  type: "image" | "text";
  assetId?: string;
  text?: string;
  caption?: string;
};

export type ArchitectureNodeBase = {
  id: string;
  title: string;
  description?: string;
  position: XYPosition;
  size?: ArchitectureSize;
  parentModuleId?: string;
  groupId?: string;
  annotationIds?: string[];
  sourceIds?: string[];
};

export type ModuleNode = ArchitectureNodeBase & {
  type: "module";
  symbol?: string;
  architecture?: string;
  keywords?: string[];
};

export type DataNode = ArchitectureNodeBase & {
  type: "data";
  symbol?: string;
  preprocessing?: string[];
  examples?: DataExample[];
  primaryExampleId?: string;
};

export type ArchitectureNode = ModuleNode | DataNode;

export type TensorEntity = {
  id: string;
  name?: string;
  symbol?: string;
  shape?: string;
  description?: string;
  sourceIds?: string[];
};

export type ArchitectureEdge = {
  id: string;
  source: string;
  target: string;
  tensorId?: string;
  kind?: "flow" | "conditioning";
  label?: string;
  sourceAnchor?: "left" | "right" | "top" | "bottom";
  targetAnchor?: "left" | "right" | "top" | "bottom";
  sourceIds?: string[];
};

export type ArchitectureAnnotation = {
  id: string;
  target?: { type: "node" | "edge" | "tensor"; id: string };
  title?: string;
  markdown: string;
  createdBy?: "user" | "agent";
  sourceIds?: string[];
};

export type ArchitectureGroup = {
  id: string;
  title: string;
  position: XYPosition;
  size: ArchitectureSize;
};

export type ArchitectureScene = {
  schemaVersion: 1;
  layoutEpoch: number;
  nodes: ArchitectureNode[];
  tensors: TensorEntity[];
  edges: ArchitectureEdge[];
  annotations: ArchitectureAnnotation[];
  groups: ArchitectureGroup[];
  sources: ArchitectureSource[];
  assets: ArchitectureAsset[];
};

export type ArchitectureDiagnostic = {
  level: "error" | "warning";
  code: string;
  message: string;
  entityId?: string;
};

export type ArchitectureDocument = {
  paperId: string;
  status: "missing" | "generating" | "ready" | "failed";
  revision: number;
  schemaVersion: number;
  scene?: ArchitectureScene | null;
  taskId?: string | null;
  progress?: {
    activities?: Array<{
      id: string;
      kind: string;
      label: string;
      detail?: string;
      status: "running" | "completed" | "failed" | "cancelled";
    }>;
    updatedAt?: string;
  } | null;
  error?: string | null;
  lock?: {
    ownerType: "user" | "agent";
    ownerId: string;
    expiresAt?: string | null;
  } | null;
  updatedAt?: string | null;
  sourcePdfVersion?: string | null;
  currentPdfVersion?: string | null;
  stale?: boolean;
};

export type ArchitectureCommand =
  | { type: "focus" | "expand" | "collapse"; target: string }
  | { type: "highlight" | "highlight-path"; targets: string[] }
  | { type: "dim-others"; except: string[] }
  | { type: "show-tensor"; tensorId: string }
  | { type: "clear-transient" };

export type ArchitectureReaderContext = {
  revision: number;
  selectedNodeIds: string[];
  expandedNodeIds: string[];
};

export type ArchitectureChatCard = {
  id: string;
  type: "module" | "data";
  title: string;
  symbol?: string;
  architecture?: string;
  details: string[];
  description?: string;
  annotation?: string;
};

export function isArchitectureScene(value: unknown): value is ArchitectureScene {
  if (!value || typeof value !== "object") return false;
  const scene = value as Partial<ArchitectureScene>;
  if (scene.schemaVersion !== 1 || !Number.isInteger(scene.layoutEpoch)) return false;
  if (![scene.nodes, scene.tensors, scene.edges, scene.annotations, scene.groups, scene.sources, scene.assets].every(Array.isArray)) return false;
  return scene.nodes!.every((node) => {
    if (!node || typeof node !== "object") return false;
    const candidate = node as Partial<ArchitectureNode>;
    return typeof candidate.id === "string"
      && (candidate.type === "module" || candidate.type === "data")
      && typeof candidate.title === "string"
      && Number.isFinite(candidate.position?.x)
      && Number.isFinite(candidate.position?.y);
  });
}
