export type NodeRef = {
  id: string;
  name: string;
  reason?: string | null;
  is_key?: boolean;
};

export type Tag = { id: string; name: string; color?: string | null };

export type Paper = {
  id: string;
  title: string;
  abstract?: string | null;
  authors?: Array<{ name: string }>;
  first_author?: string | null;
  corresponding_author?: string | null;
  primary_affiliation?: string | null;
  source: string;
  source_url?: string | null;
  pdf_url?: string | null;
  doi?: string | null;
  published_at?: string | null;
  created_at?: string | null;
  status: string;
  summary?: string | null;
  agent_comment?: string | null;
  problem?: string | null;
  innovation?: string | null;
  experiment?: string | null;
  limitations?: string | null;
  research_score?: number | null;
  relevance_score?: number | null;
  recommendation_reason?: string | null;
  is_interested: boolean;
  is_quality: boolean;
  completion_summary?: string | null;
  pending_feedback?: Record<string, unknown>;
  metadata_json?: Record<string, unknown>;
  analysis_status?: "queued" | "processing" | "completed" | "failed" | null;
  analysis_error?: string | null;
  suggested_tags?: string[];
  tags: Tag[];
  nodes: NodeRef[];
  has_pdf?: boolean;
  pdf_version?: string | null;
};

export type BriefItem = {
  id: string;
  rank: number;
  tier: "featured" | "related" | "manual";
  paper: Paper;
  current_status: string;
  is_interested: boolean;
  is_quality: boolean;
};

export type Brief = {
  id: string;
  brief_date: string;
  title: string;
  introduction?: string | null;
  status: string;
  published_at?: string | null;
  item_count?: number;
  items: BriefItem[];
};

export type ResearchNode = {
  id: string;
  name: string;
  core_question: string;
  summary?: string | null;
  solved?: string | null;
  open_questions?: string | null;
  domain?: string | null;
  color?: string | null;
  layout_x?: number | null;
  layout_y?: number | null;
  paper_count: number;
  parents?: NodeRef[];
  children?: NodeRef[];
  papers?: Array<Pick<Paper, "id" | "title" | "first_author" | "published_at" | "status" | "is_quality"> & { reason?: string; is_key?: boolean }>;
  progress?: Array<{ id: string; title: string; content: string; happened_at?: string; created_at: string }>;
  ideas?: Array<{ id: string; content: string; status: string; note?: string; created_at: string }>;
};

export type GraphData = {
  focus_id?: string | null;
  nodes: ResearchNode[];
  links: Array<{ id: string; source_id: string; target_id: string }>;
  boundary_nodes?: Array<ResearchNode & { connects_to: string[] }>;
  has_more?: boolean;
};

export type Annotation = {
  id: string;
  paper_id: string;
  kind: "highlight" | "note" | "translation";
  page_number: number;
  selected_text?: string | null;
  content?: string | null;
  position_json: {
    rects?: number[][];
    block_id?: string;
    start_offset?: number;
    end_offset?: number;
    segments?: Array<{ block_id: string; start_offset: number; end_offset: number }>;
  };
  color?: string | null;
  created_at: string;
};

export type DailyAnnotation = Pick<Annotation, "id" | "kind" | "page_number" | "selected_text" | "content" | "color" | "created_at">;
export type DailyPayload = {
  date: string;
  available_dates: string[];
  selection: null | {
    date: string;
    paper: Paper & { arxiv_id?: string | null };
    annotations: DailyAnnotation[];
  };
};

export type DocumentBlock = {
  id: string;
  anchor?: string | null;
  kind: string;
  page_number: number;
  bbox?: { left: number; top: number; right: number; bottom: number } | null;
  level: number;
  text?: string;
  caption?: string;
  html?: string;
  image_url?: string;
};

export type PaperDocument = {
  paper_id: string;
  version?: string;
  status: "missing" | "processing" | "ready" | "failed";
  schema_version?: number;
  title?: string;
  reader_source?: "arxiv_html" | "docling";
  source_url?: string;
  blocks?: DocumentBlock[];
  error?: string;
  retryable?: boolean;
  phase?: "queued" | "preparing" | "source" | "parsing" | "structuring" | "assets" | "finalizing";
  phase_label?: string;
  progress?: number;
  stage?: number;
  stage_count?: number;
  page_count?: number;
  processed_items?: number;
  total_items?: number;
  started_at?: string;
  estimated_seconds_low?: number;
  estimated_seconds_high?: number;
  updated_at?: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations_json: Array<{ paper_id?: string; page_number: number; end_page_number?: number; label: string; marker: string }>;
  context_json: Record<string, unknown>;
  created_at: string;
};

export type Conversation = {
  id: string;
  kind: "global" | "paper";
  reference_id?: string | null;
  title?: string | null;
  status: string;
  messages: Message[];
};

export type AgentRuntime = {
  model?: string;
  reasoning_effort?: string;
  model_provider?: string;
};

export type AgentUsage = {
  current_tokens?: number;
  context_window?: number;
  total_tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_input_tokens?: number;
};

export type AgentRuntimeState = {
  runtime?: AgentRuntime;
  usage?: AgentUsage;
};

export type ServiceCheck = {
  service: "codex" | "translation" | "arxiv";
  ok: boolean;
  status: "ok" | "error" | "disabled";
  latency_ms: number;
  message: string;
  checked_at: string;
  model?: string;
  reasoning_effort?: string;
  model_provider?: string;
  context_window?: number;
};

export type LatestServiceChecks = Record<ServiceCheck["service"], ServiceCheck | null>;

export type TaskRun = {
  id: string;
  kind: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  payload_json: Record<string, unknown>;
  result_json: { response?: string };
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

export type OperationLog = {
  id: string;
  actor: string;
  action: string;
  entity_type?: string | null;
  entity_id?: string | null;
  task_id?: string | null;
  detail_json: Record<string, unknown>;
  created_at: string;
};

export type Memory = {
  id: string;
  kind: "interest_profile" | "agent_experience" | string;
  title: string;
  content: string;
  source?: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type Setting = { key: string; value_json: unknown; description?: string | null };
