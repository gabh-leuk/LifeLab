export interface User {
  id: number;
  username: string;
  display_name: string | null;
  is_demo: boolean;
}

export interface LoginResponse {
  token: string; // 明文只在这里出现一次
  expires_at: string | null;
  user: User;
}

export interface EventItem {
  id: number;
  user_id: number;
  timestamp: string;
  type: string;
  source: string;
  confidence: number;
  note: string | null;
  category: string | null;
  tags: string[];
  device_id: number | null;
  ended_at: string | null;
}

/** 某本地日的记录统计：条数 + 连续记录天数（非 AI 的确定数字）。 */
export interface RecordStats {
  date: string;
  today_count: number;
  streak_days: number;
}

export interface EventTypeItem {
  key: string;
  label: string;
  category: string;
  icon: string | null;
  order: number;
  id: number | null;
  builtin: boolean;
  archived: boolean;
}

export interface AppRuleItem {
  id: number;
  match_type: "exact" | "substring";
  scope: "app" | "domain" | "any";
  match_value: string; // 已小写；domain 规则已归并到根域
  category: string;
  display_label: string | null; // 非空则作为展示名（改名，不合并行）
  priority: number;
  enabled: boolean;
}

export interface UnclassifiedApp {
  app: string; // 采集到的原名（规则的 match_value 用它）
  label: string | null;
  platform: string | null;
  seconds: number;
  sessions: number;
  category: string;
}

export interface ThoughtItem {
  id: number;
  user_id: number;
  content: string;
  tags: string[];
  source: string;
  timestamp: string;
}

export interface StateItem {
  id: number;
  user_id: number;
  energy: number;
  focus: number;
  irritation: number;
  timestamp: string;
}

export type TimelineData = EventItem | ThoughtItem | StateItem;

export interface SegmentApp {
  platform: string; // pc | android
  app: string;
  label: string | null;
  seconds: number;
  category: string; // 按当前规则推导的大类（就地编辑的默认值）
}

export interface DeviceActivity {
  platform: string; // pc | android
  category: string;
  label: string | null;
  start: string;
  end: string;
  seconds: number;
}

export interface EventSegment {
  kind: "event" | "device";
  event: EventItem;
  platform: string | null; // 只有设备段有：这一段是哪类设备（pc | android）
  start: string;
  end: string;
  duration_minutes: number;
  end_boundary: "next_event" | "explicit" | "day_end";
  end_inferred: boolean;
  fuzzy: boolean;
  fuzzy_reason: string | null;
  device_apps: SegmentApp[]; // 该时段使用的设备应用（Top N）
  device_activities: DeviceActivity[]; // 该时段包含的设备真实活动段
}

export interface FloatingItem {
  kind: "thought" | "state";
  timestamp: string;
  data: ThoughtItem | StateItem;
}

// 设备自动段按「设备 × 本地小时」合并后的粗粒度视图
export interface DeviceHour {
  hour: number; // 本地小时 0..23
  start: string;
  end: string;
  seconds: number;
  categories: string[]; // 按摊销秒数降序，首个为主导
  category_seconds: Record<string, number>; // 大类 → 该小时摊销秒数
  apps: SegmentApp[];
  // 该小时包含的设备活动段（含落在人工段内的）
  activities: DeviceActivity[];
}

export interface TimelineResponse {
  date: string;
  segments: EventSegment[];
  floatings: FloatingItem[];
  device_segments: EventSegment[]; // 未被人工记录覆盖的设备自动时间段
  device_hours: DeviceHour[]; // 上者按小时合并（前端主列表）
}

export type ExperimentStatus =
  | "DRAFT"
  | "RUNNING"
  | "PAUSED"
  | "COMPLETED"
  | "CONCLUDED";

export type Verdict =
  | "SUPPORTED"
  | "PARTIALLY_SUPPORTED"
  | "REFUTED"
  | "INCONCLUSIVE";

export type MetricDirection = "up_good" | "down_good" | "neutral";

export interface MetricDef {
  key: string;
  name: string;
  unit?: string | null;
  direction: MetricDirection;
  source?: string; // manual | event_duration:XXX | event_count:XXX | state_avg:focus
}

/** 区间内用过的应用/网站 key —— 指标来源选择器的候选（`app` 原样可用）。 */
export interface KnownApp {
  app: string;
  label: string | null;
  platform: string | null;
  seconds: number;
}

export interface AggregateResult {
  experiment_id: number;
  created: number;
  updated: number;
  deleted: number;
  days: number;
  metrics: string[];
}

export interface ExperimentStatusEvent {
  id: number;
  experiment_id: number;
  from_status: ExperimentStatus | null;
  to_status: ExperimentStatus;
  reason: string | null;
  created_at: string;
}

export interface ExperimentLog {
  id: number;
  user_id: number;
  experiment_id: number;
  metric: string;
  value: number;
  note: string | null;
  timestamp: string;
  source: string;
  created_at: string;
}

export interface MetricStats {
  metric: string;
  unit: string | null;
  count: number;
  mean: number | null;
  min: number | null;
  max: number | null;
  first: number | null;
  latest: number | null;
  change: number | null;
  first_half_mean: number | null;
  second_half_mean: number | null;
  trend: "up" | "down" | "flat";
  direction: MetricDirection;
  improved: boolean | null;
}

export interface ExperimentStats {
  experiment_id: number;
  total_logs: number;
  metric_stats: MetricStats[];
  running_days: number | null;
  paused_total_days: number | null;
  days_elapsed: number | null;
}

export interface Experiment {
  id: number;
  user_id: number;
  name: string;
  question: string | null;
  hypothesis: string | null;
  variable: string | null;
  indicator: string | null;
  metrics: MetricDef[] | null;
  expected_days: number | null;
  baseline_note: string | null;
  status: ExperimentStatus;
  started_at: string | null;
  ended_at: string | null;
  completion_analysis: string | null;
  result_verdict: Verdict | null;
  conclusion: string | null;
  conclusion_reason: string | null;
  conclusion_confidence: number | null;
  concluded_at: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExperimentDetail extends Experiment {
  status_events: ExperimentStatusEvent[];
  stats: ExperimentStats | null;
}

export interface ExperimentStatusChange {
  status: ExperimentStatus;
  reason?: string;
  completion_analysis?: string;
  verdict?: Verdict;
  conclusion?: string;
  conclusion_reason?: string;
  conclusion_confidence?: number;
  write_finding?: boolean;
  problem_id?: number;
}

// ── 用户背景（画像层：常驻注入，用户确认制） ─────────────
export type ProfileCategory =
  | "identity"
  | "habit"
  | "preference"
  | "context"
  | "constraint";

export type ProfileStatus = "active" | "archived";

export interface ProfileFact {
  id: number;
  user_id: number;
  category: ProfileCategory;
  content: string;
  source: "manual" | "ai";
  source_ref: string | null;
  confidence: number | null;
  status: ProfileStatus;
  last_confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

// ── 画像晋升（跨周/跨天复现 → 候选 → 确认写入） ──────────
export interface PromotionMember {
  kind: string;
  ref: string | null;
  content: string;
  similarity: number | null;
}

export interface PromotionCandidate {
  content: string;
  category: ProfileCategory;
  confidence: number;
  source_ref: string | null;
  source_kind: string;
  spans: string[];
  avg_similarity: number;
  members: PromotionMember[];
}

export interface PromotionScanResult {
  candidates: PromotionCandidate[];
  scanned_patterns: number;
  scanned_insights: number;
  mode: "refined" | "raw";
}

export type ProblemStatus = "OPEN" | "DORMANT" | "RESOLVED";

export interface Problem {
  id: number;
  user_id: number;
  title: string;
  category: string | null;
  note: string | null;
  status: ProblemStatus;
  created_at: string;
  updated_at: string;
}

export type FindingKind = "OBSERVATION" | "HYPOTHESIS" | "CONCLUSION";

export interface Finding {
  id: number;
  user_id: number;
  problem_id: number | null;
  kind: FindingKind;
  title: string;
  observation: string;
  evidence: string | null;
  interpretation: string | null;
  confidence: number;
  next_step: string | null;
  created_at: string;
}

export interface MemoryItem {
  id: number;
  user_id: number;
  kind: string;
  content: string;
  source_ref: string | null;
  created_at: string;
  has_embedding: boolean;
}

// ── AI 实验设计（问题 → 草稿 → 用户确认创建） ─────────────
export interface DraftEvidence {
  kind: string;
  ref: string | null;
  title: string | null;
  similarity: number | null;
}

export interface ExperimentDraft {
  problem_id: number;
  name: string;
  question: string;
  hypothesis: string;
  variable: string;
  indicator: string;
  metrics: MetricDef[];
  expected_days: number;
  baseline_note: string | null;
  rationale: string;
  evidence: DraftEvidence[];
}

export interface MemoryExtractResponse {
  date: string;
  insights: MemoryItem[];
  skipped: boolean;
}

export interface MemoryPatternExtractResponse {
  week: string; // 2026-W37
  week_start: string;
  week_end: string;
  patterns: MemoryItem[];
  skipped: boolean;
}

// ── 设备采集使用概览（记忆页只读卡片） ─────────────────────
export interface UsageOverviewApp {
  app: string;
  label: string | null;
  seconds: number;
  switches: number;
  category: string; // 按当前规则推导的大类（就地编辑的默认值）
}

export interface UsageOverviewHour {
  hour: number; // 0..23
  seconds: number;
  apps: UsageOverviewApp[]; // 该小时 Top N 应用（供悬浮展示）
}

export interface UsageOverviewDevice {
  device_id: number;
  name: string;
  platform: string;
  total_seconds: number;
  hours: UsageOverviewHour[]; // 完整 24 槽
  apps: UsageOverviewApp[];
  websites: UsageOverviewApp[]; // 浏览器站点（app 形如 web:<域名>），不含在 total_seconds
}

export interface UsageOverviewResponse {
  date: string;
  devices: UsageOverviewDevice[];
}

function resolveApiBase(): string {
  const envBase = import.meta.env.VITE_API_BASE;
  if (envBase) return envBase;
  // 生产：前端由 FastAPI 同源托管（frontend/dist），走相对路径即可 ——
  // 经 Tailscale Funnel 访问时 hostname 是 *.ts.net，绝对地址会拼成
  // https://…ts.net:8000 而 Funnel 只暴露 443，必然请求失败。
  if (!import.meta.env.DEV) return "";
  // 开发：手机经 Tailscale IP 访问 vite 时，后端同机、同 IP、端口 8000；
  // 本机访问仍走 127.0.0.1，避免监听 0.0.0.0 时多一层网络。
  const { protocol, hostname } = window.location;
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1") {
    return "http://127.0.0.1:8000";
  }
  return `${protocol}//${hostname}:8000`;
}

const API_BASE = resolveApiBase();

// 会话令牌由 auth.ts 注入。用 setter 而不是 import auth.ts，避免循环依赖
// （auth.ts 要 import api.ts 才能登录/回填）。
let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthHeaderToken(token: string | null): void {
  authToken = token;
}

/** 收到 401 时的统一处理（清会话 → 订阅者重渲染 → 守卫重定向）。 */
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 在唯一出口合并请求头：连 DELETE 这类不走 json() 的调用也自动带上令牌。
  const headers = new Headers(init?.headers);
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  // 登录接口自身的 401 是「用户名或密码错误」，不能触发全局登出
  if (res.status === 401 && path !== "/auth/login") {
    onUnauthorized?.();
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) {
    return undefined as unknown as T; // 无内容响应
  }
  return res.json() as Promise<T>;
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// 本地时区偏移（分钟，东八区 = +480）：后端按它切本地日
function tzOffset(): number {
  return -new Date().getTimezoneOffset();
}

export function today(): string {
  // 用本地时间而非 UTC（toISOString 在东八区凌晨会差一天）
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export const api = {
  // 会话：登录失败时后端返回 401 + detail，request() 会抛成 Error
  login: (username: string, password: string) =>
    request<LoginResponse>("/auth/login", json("POST", { username, password })),

  me: () => request<User>("/auth/me"),

  logout: () => request<null>("/auth/logout", { method: "POST" }),

  createEvent: (type: string, note?: string, timestamp?: string, tags?: string[]) =>
    request<EventItem>(
      "/events",
      json("POST", { type, note: note || undefined, timestamp, tags }),
    ),

  endEvent: (id: number, endedAt?: string) =>
    request<EventItem>(`/events/${id}/end`, json("PATCH", endedAt ? { ended_at: endedAt } : {})),

  // 就地修订：只改传入的字段（note 传 null 表示清空）
  updateEvent: (
    id: number,
    patch: { type?: string; note?: string | null; category?: string; tags?: string[] },
  ) => request<EventItem>(`/events/${id}`, json("PATCH", patch)),

  deleteEvent: (id: number) =>
    request<null>(`/events/${id}`, { method: "DELETE" }),

  // 某日的记录（默认今天，按本地时区切日）—— 记录页的「最近记录」用
  listEvents: (date: string = today(), limit = 50) => {
    const params = new URLSearchParams({
      date,
      tz_offset: String(tzOffset()),
      limit: String(limit),
    });
    return request<EventItem[]>(`/events?${params.toString()}`);
  },

  // 记录页的即时回报：今天条数 + 连续天数
  recordStats: (date: string = today()) => {
    const params = new URLSearchParams({
      date,
      tz_offset: String(tzOffset()),
    });
    return request<RecordStats>(`/events/record-stats?${params.toString()}`);
  },

  // 事件类型清单：内置 + 自定义（含归档项，供历史记录解析标签/图标）
  listEventTypes: () =>
    request<EventTypeItem[]>("/event-types?include_archived=true"),

  createEventType: (payload: { label: string; category: string; icon?: string | null }) =>
    request<EventTypeItem>("/event-types", json("POST", payload)),

  updateEventType: (
    id: number,
    patch: { label?: string; category?: string; icon?: string | null; archived?: boolean },
  ) => request<EventTypeItem>(`/event-types/${id}`, json("PATCH", patch)),

  archiveEventType: (id: number) =>
    request<null>(`/event-types/${id}`, { method: "DELETE" }),

  createThought: (content: string, tags: string[]) =>
    request<ThoughtItem>("/thoughts", json("POST", { content, tags })),

  createState: (s: { energy: number; focus: number; irritation: number }) =>
    request<StateItem>("/states", json("POST", s)),

  timeline: (date: string) => {
    // 客户端本地时区偏移（分钟），东八区 = +480。后端按此"分天"，保证当天结束=本地午夜
    const tzOffset = -new Date().getTimezoneOffset();
    return request<TimelineResponse>(`/timeline/${date}?tz_offset=${tzOffset}`);
  },

  experiments: () => request<Experiment[]>("/experiments"),

  experimentDetail: (id: number) => request<ExperimentDetail>(`/experiments/${id}`),

  createExperiment: (e: {
    name: string;
    question?: string;
    hypothesis?: string;
    variable?: string;
    indicator?: string;
    metrics?: MetricDef[];
    expected_days?: number;
    baseline_note?: string;
    infer_keys?: string[];
  }) => request<Experiment>("/experiments", json("POST", e)),

  updateExperiment: (
    id: number,
    e: {
      name?: string;
      question?: string;
      hypothesis?: string;
      variable?: string;
      indicator?: string;
      metrics?: MetricDef[];
      expected_days?: number;
      baseline_note?: string;
      completion_analysis?: string;
      note?: string;
      infer_keys?: string[];
    },
  ) => request<Experiment>(`/experiments/${id}`, json("PATCH", e)),

  /** 把指标名识别成数据源（无状态、可反复调）；失败后端返 502。 */
  inferMetricSources: (metrics: { key: string; name: string; unit?: string | null }[]) =>
    request<{ sources: Record<string, string> }>(
      "/experiments/infer-sources",
      json("POST", { metrics }),
    ),

  setExperimentStatus: (id: number, payload: ExperimentStatusChange) =>
    request<Experiment>(`/experiments/${id}/status`, json("POST", payload)),

  revertExperiment: (id: number) =>
    request<Experiment>(`/experiments/${id}/revert`, { method: "POST" }),

  concludeExperiment: (id: number, payload: ExperimentStatusChange) =>
    request<{ experiment: Experiment; finding_id: number | null }>(
      `/experiments/${id}/conclude`,
      json("POST", payload),
    ),

  experimentLogs: (id: number, metric?: string) =>
    request<ExperimentLog[]>(
      metric
        ? `/experiments/${id}/logs?metric=${encodeURIComponent(metric)}`
        : `/experiments/${id}/logs`,
    ),

  createExperimentLog: (
    id: number,
    log: { metric: string; value: number; note?: string; timestamp?: string },
  ) => request<ExperimentLog>(`/experiments/${id}/logs`, json("POST", log)),

  createExperimentLogs: (
    id: number,
    logs: { metric: string; value: number; note?: string; timestamp?: string }[],
  ) =>
    request<ExperimentLog[]>(
      `/experiments/${id}/logs/batch`,
      json("POST", { logs }),
    ),

  deleteExperimentLog: (id: number, logId: number) =>
    request<null>(`/experiments/${id}/logs/${logId}`, { method: "DELETE" }),

  experimentStats: (id: number) => request<ExperimentStats>(`/experiments/${id}/stats`),

  aggregateExperiment: (id: number, tzOffset?: number) =>
    request<AggregateResult>(
      `/experiments/${id}/aggregate?tz_offset=${tzOffset ?? -new Date().getTimezoneOffset()}`,
      { method: "POST" },
    ),

  experimentStatusEvents: (id: number) =>
    request<ExperimentStatusEvent[]>(`/experiments/${id}/status-events`),

  deleteExperiment: (id: number) =>
    request<null>(`/experiments/${id}`, { method: "DELETE" }),

  problems: () => request<Problem[]>("/problems"),

  createProblem: (p: { title: string; category?: string; note?: string }) =>
    request<Problem>("/problems", json("POST", p)),

  updateProblem: (
    id: number,
    p: { title?: string; category?: string; note?: string; status?: ProblemStatus },
  ) => request<Problem>(`/problems/${id}`, json("PATCH", p)),

  deleteProblem: (id: number) =>
    request<null>(`/problems/${id}`, { method: "DELETE" }),

  findings: (problemId?: number) =>
    request<Finding[]>(
      problemId ? `/findings?problem_id=${problemId}` : "/findings",
    ),

  createFinding: (f: {
    problem_id?: number;
    kind: FindingKind;
    title: string;
    observation: string;
    evidence?: string;
    interpretation?: string;
    confidence?: number;
    next_step?: string;
  }) => request<Finding>("/findings", json("POST", f)),

  updateFinding: (
    id: number,
    f: {
      kind?: FindingKind;
      title?: string;
      observation?: string;
      evidence?: string;
      interpretation?: string;
      confidence?: number;
      next_step?: string;
    },
  ) => request<Finding>(`/findings/${id}`, json("PATCH", f)),

  deleteFinding: (id: number) =>
    request<null>(`/findings/${id}`, { method: "DELETE" }),

  // ── 用户背景（画像层） ────────────────────────────────
  profileFacts: (includeArchived = false) =>
    request<ProfileFact[]>(`/profile/facts?include_archived=${includeArchived}`),

  createProfileFact: (f: { category: ProfileCategory; content: string }) =>
    request<ProfileFact>("/profile/facts", json("POST", f)),

  updateProfileFact: (
    id: number,
    f: { category?: ProfileCategory; content?: string; status?: ProfileStatus },
  ) => request<ProfileFact>(`/profile/facts/${id}`, json("PATCH", f)),

  deleteProfileFact: (id: number) =>
    request<null>(`/profile/facts/${id}`, { method: "DELETE" }),

  scanPromotions: () =>
    request<PromotionScanResult>("/profile/promotions/scan", json("POST", {})),

  commitPromotions: (candidates: PromotionCandidate[]) =>
    request<{ fact_ids: number[]; skipped: number }>(
      "/profile/promotions/commit",
      json("POST", { candidates }),
    ),

  // ── 记忆检索（Memory/RAG） ─────────────────────────────
  memoryList: (limit = 50, kind?: string, sourceRef?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (kind) params.set("kind", kind);
    if (sourceRef) params.set("source_ref", sourceRef);
    return request<MemoryItem[]>(`/memory/items?${params.toString()}`);
  },

  designExperiment: (problemId: number) =>
    request<ExperimentDraft>(
      "/analysis/experiment-draft",
      json("POST", { problem_id: problemId }),
    ),

  createMemory: (m: { kind: string; content: string; source_ref?: string }) =>
    request<MemoryItem>("/memory/items", json("POST", m)),

  memorySync: () => request<{ synced: number }>("/memory/sync", json("POST", {})),

  memoryExtract: (date: string, tzOffset?: number) =>
    request<MemoryExtractResponse>(
      "/memory/extract",
      json("POST", { date, tz_offset: tzOffset ?? -new Date().getTimezoneOffset() }),
    ),

  memoryExtractByDate: (date: string) =>
    request<MemoryExtractResponse>(`/memory/extract/${date}`),

  memoryExtractWeek: (date: string, force = false) =>
    request<MemoryPatternExtractResponse>(
      "/memory/extract-week",
      json("POST", { date, tz_offset: -new Date().getTimezoneOffset(), force }),
    ),

  memoryExtractWeekByDate: (date: string) =>
    request<MemoryPatternExtractResponse>(`/memory/extract-week/${date}`),

  deviceUsageOverview: (date: string) =>
    request<UsageOverviewResponse>(`/devices/usage-overview/${date}`),

  rebuildActivity: (date: string) => {
    const tzOffset = -new Date().getTimezoneOffset();
    return request<{ date: string; devices: number; deleted: number; created: number }>(
      `/devices/rebuild-activity?date=${date}&tz_offset=${tzOffset}`,
      { method: "POST" },
    );
  },

  // 区间重算走后台任务：返回 202 后前端稍后再刷新即可看到结果
  rebuildActivityRange: (start: string, end: string) =>
    request<{ queued: boolean; start: string; end: string; days: number }>(
      `/devices/rebuild-activity-range?start=${start}&end=${end}&tz_offset=${tzOffset()}`,
      { method: "POST" },
    ),

  // 仍未认出大类的应用（供逐条标记）
  uncategorizedApps: (start: string, end: string, minSeconds = 60) =>
    request<UnclassifiedApp[]>(
      `/devices/uncategorized-apps?start=${start}&end=${end}&min_seconds=${minSeconds}`,
    ),

  // 区间内用过的全部应用/网站 key（含 web:<域名>）—— 指标来源选择器的候选
  knownApps: (start: string, end: string, limit = 60) =>
    request<KnownApp[]>(`/devices/known-apps?start=${start}&end=${end}&limit=${limit}`),

  listAppRules: () => request<AppRuleItem[]>("/app-rules"),

  createAppRule: (payload: {
    match_value: string;
    category: string;
    match_type?: "exact" | "substring";
    scope?: "app" | "domain" | "any";
    display_label?: string | null;
    priority?: number;
  }) => request<AppRuleItem>("/app-rules", json("POST", payload)),

  updateAppRule: (
    id: number,
    patch: {
      match_value?: string;
      category?: string;
      match_type?: "exact" | "substring";
      scope?: "app" | "domain" | "any";
      display_label?: string | null;
      priority?: number;
      enabled?: boolean;
    },
  ) => request<AppRuleItem>(`/app-rules/${id}`, json("PATCH", patch)),

  deleteAppRule: (id: number) =>
    request<null>(`/app-rules/${id}`, { method: "DELETE" }),

  // ── Agent（M6）：会话式，状态在后端 checkpointer 里 ──────────

  agentRun: (payload: { query: string; thread_id?: string }) =>
    // 复盘按本地时区分天，tz_offset 跟着请求走（恢复时后端从 checkpoint 取回）
    request<AgentThreadState>(
      "/agent/run",
      json("POST", { ...payload, tz_offset: tzOffset() }),
    ),

  agentResume: (payload: {
    thread_id: string;
    approve: boolean;
    args?: Record<string, unknown> | null;
    note?: string | null;
  }) => request<AgentThreadState>("/agent/resume", json("POST", payload)),

  agentThreads: () => request<AgentThreadItem[]>("/agent/threads"),

  agentThreadState: (threadId: string) =>
    request<AgentThreadState>(`/agent/threads/${encodeURIComponent(threadId)}`),

  // ── 复盘：索引 / 详情 / 生成 / 删除 ───────────────────────
  // 索引是日+周+月合成的一个列表；正文仍走各自的详情端点，不重复传输。
  reviewsIndex: () => request<ReviewIndexItem[]>("/reviews"),

  getReview: (date: string) =>
    request<ReviewRead>(`/reviews/${encodeURIComponent(date)}`),

  // date 是该期间内的任意一天（周按 ISO 对齐、月按自然月），不是 period_key
  getPeriodReview: (periodType: "week" | "month", date: string) =>
    request<PeriodReviewRead>(
      `/reviews/period/${periodType}/${encodeURIComponent(date)}`,
    ),

  generateReview: (date: string, force = false) =>
    request<ReviewRead>(
      `/reviews?tz_offset=${tzOffset()}`,
      json("POST", { date, force }),
    ),

  generatePeriodReview: (
    periodType: "week" | "month",
    date: string,
    force = false,
  ) =>
    request<PeriodReviewRead>(
      "/reviews/period",
      json("POST", {
        period_type: periodType,
        date,
        force,
        tz_offset: tzOffset(),
      }),
    ),

  deleteReview: (date: string) =>
    request<void>(`/reviews/${encodeURIComponent(date)}`, { method: "DELETE" }),
};

export interface AgentStep {
  tool: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  // null = 只读工具，不经审批
  approved: boolean | null;
}

export interface AgentPendingCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  summary: string;
}

export interface AgentMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AgentThreadState {
  thread_id: string;
  status: "answer" | "interrupt";
  answer: string;
  messages: AgentMessage[];
  steps: AgentStep[];
  pending: AgentPendingCall[];
}

export interface AgentThreadItem {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** 复盘索引的一项：日/周/月同构，一个列表渲染三档。 */
export interface ReviewIndexItem {
  kind: "day" | "week" | "month";
  /** 日 = "2026-09-16"；周 = "2026-W37"；月 = "2026-09" */
  key: string;
  label: string;
  /** 仅周/月：回查详情用的日期（该期间内任意一天） */
  period_start: string | null;
  summary: string;
  status: string;
  /** 仅周/月：期间素材在生成后有更新 */
  stale: boolean;
  updated_at: string;
}

export interface ReviewRead {
  id: number;
  user_id: number;
  date: string;
  review_text: string;
  structured: Record<string, unknown> | null;
  model: string | null;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PeriodReviewRead {
  id: number;
  user_id: number;
  period_type: "week" | "month";
  period_key: string;
  period_start: string;
  period_end: string;
  review_text: string;
  structured: Record<string, unknown> | null;
  model: string | null;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  stale: boolean;
  material_count: number;
}
