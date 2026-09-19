import { useEffect, useState } from "react";
import { RulerIcon, SparkleIcon, TrashIcon } from "@phosphor-icons/react";
import { api, today } from "../../lib/api";
import type { KnownApp, MetricDef, MetricDirection } from "../../lib/api";
import { METRIC_SOURCES, metricSourceLabel } from "../../lib/constants";

export const DIRECTION_LABELS: Record<MetricDirection, string> = {
  up_good: "越高越好",
  down_good: "越低越好",
  neutral: "中性",
};

interface SourceOption {
  value: string;
  label: string;
}

const BASE_OPTIONS: SourceOption[] = METRIC_SOURCES.map((s) => ({
  value: s.value,
  label: s.label,
}));

const emptyDraft = (): MetricDef => ({
  key: "",
  name: "",
  unit: "",
  direction: "neutral",
  source: "manual",
});

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * 结构化指标编辑器：已选指标清单 + 新增行。创建实验与编辑实验共用。
 *
 * **来源（source）默认不显示** —— 用户只写名字，由 AI 识别填 source。
 * 展开的「数据源」面板是纠正识别错误的唯一入口，也是手动指定来源的地方。
 * `onInferKeysChange` 把「还没定过来源的 key」报给父组件，父组件原样提交为
 * `infer_keys`（后端只认显式提名的那些，见 `app/routers/experiments.py`）。
 */
export default function MetricEditor({
  metrics,
  onChange,
  onInferKeysChange,
  initialResolved,
}: {
  metrics: MetricDef[];
  onChange: (next: MetricDef[]) => void;
  onInferKeysChange?: (keys: string[]) => void;
  /** 一进来就算「来源已定」的 key —— 编辑既有实验时传全部已有指标，
   * 免得每次编辑都把用户认可过的来源重识别一遍。 */
  initialResolved?: string[];
}) {
  const [draft, setDraft] = useState<MetricDef>(emptyDraft);
  const [err, setErr] = useState<string | null>(null);
  // 已经定过来源的指标 key（识别过、或用户手动改过）；剩下的才是待识别的
  const [resolved, setResolved] = useState<Set<string>>(
    () => new Set(initialResolved ?? []),
  );
  const [showSources, setShowSources] = useState(false);
  const [inferring, setInferring] = useState(false);
  const [knownApps, setKnownApps] = useState<KnownApp[]>([]);
  const [appsLoaded, setAppsLoaded] = useState(false);

  useEffect(() => {
    onInferKeysChange?.(
      metrics.filter((m) => !resolved.has(m.key)).map((m) => m.key),
    );
  }, [metrics, resolved, onInferKeysChange]);

  function add() {
    const name = draft.name.trim();
    if (!name) return;
    const key =
      draft.key.trim() || `m${metrics.length + 1}_${name.replace(/\s+/g, "_").slice(0, 20)}`;
    if (metrics.some((m) => m.key === key)) {
      setErr(`指标 key 重复：${key}`);
      return;
    }
    onChange([
      ...metrics,
      {
        key,
        name,
        unit: draft.unit?.trim() || null,
        direction: draft.direction,
        source: "manual",
      },
    ]);
    setDraft(emptyDraft());
    setErr(null);
  }

  async function infer() {
    const pending = metrics.filter((m) => !resolved.has(m.key));
    if (!pending.length) return;
    setInferring(true);
    setErr(null);
    try {
      const { sources } = await api.inferMetricSources(
        pending.map((m) => ({ key: m.key, name: m.name, unit: m.unit ?? undefined })),
      );
      if (Object.keys(sources).length) {
        onChange(metrics.map((m) => (sources[m.key] ? { ...m, source: sources[m.key] } : m)));
      }
      // 没认出来的也标成「定过」——点一次就该够了，不必每次保存都重烧 token
      setResolved((prev) => new Set([...prev, ...pending.map((m) => m.key)]));
      setShowSources(true);
      if (!Object.keys(sources).length) {
        setErr("没能识别出可用的数据源，可在下方「数据源」里手动指定");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setInferring(false);
    }
  }

  async function toggleSources() {
    const next = !showSources;
    setShowSources(next);
    if (next && !appsLoaded) {
      setAppsLoaded(true);
      try {
        setKnownApps(await api.knownApps(daysAgo(30), today()));
      } catch {
        // 候选拉不到不影响纠正：内置清单还在
      }
    }
  }

  function setSource(key: string, source: string) {
    onChange(metrics.map((m) => (m.key === key ? { ...m, source } : m)));
    setResolved((prev) => new Set([...prev, key]));
  }

  const appOptions: SourceOption[] = knownApps.map((a) => ({
    value: `usage_duration:${a.app}`,
    label: `采集：${a.label || a.app}(分钟/天)`,
  }));
  const allOptions = [...BASE_OPTIONS, ...appOptions];

  /** 当前值不在候选里也要能选中（如某应用近期没采集到），补一条在最前 */
  function optionsFor(current?: string): SourceOption[] {
    if (current && !allOptions.some((o) => o.value === current)) {
      return [{ value: current, label: metricSourceLabel(current) }, ...allOptions];
    }
    return allOptions;
  }

  const pendingCount = metrics.filter((m) => !resolved.has(m.key)).length;

  return (
    <div className="metric-editor">
      <div className="exp-block-title">
        <RulerIcon size={14} aria-hidden="true" /> 结构化指标（可统计/画图）
      </div>
      {metrics.map((m) => (
        <div className="metric-row" key={m.key}>
          <span className="metric-chip">
            {m.name}
            {m.unit ? `(${m.unit})` : ""} · {DIRECTION_LABELS[m.direction]}
          </span>
          <button
            className="link-btn danger-link"
            aria-label={`删除指标：${m.name}`}
            onClick={() => onChange(metrics.filter((x) => x.key !== m.key))}
          >
            <TrashIcon size={14} aria-hidden="true" />
          </button>
        </div>
      ))}
      <div className="log-form">
        <input
          className="text-input"
          placeholder="名称：注意力"
          value={draft.name}
          onChange={(e) => setDraft((m) => ({ ...m, name: e.target.value }))}
        />
        <input
          className="text-input narrow"
          placeholder="单位"
          value={draft.unit ?? ""}
          onChange={(e) => setDraft((m) => ({ ...m, unit: e.target.value }))}
        />
        <select
          className="text-input date"
          value={draft.direction}
          onChange={(e) => setDraft((m) => ({ ...m, direction: e.target.value as MetricDirection }))}
        >
          {(Object.keys(DIRECTION_LABELS) as MetricDirection[]).map((d) => (
            <option key={d} value={d}>
              {DIRECTION_LABELS[d]}
            </option>
          ))}
        </select>
        <button className="secondary small" disabled={!draft.name.trim()} onClick={add}>
          添加指标
        </button>
      </div>

      {metrics.length > 0 && (
        <div className="metric-source-bar">
          <button
            className="secondary small"
            disabled={inferring || pendingCount === 0}
            onClick={infer}
            title="让 AI 判断这些指标该从哪取数"
          >
            <SparkleIcon size={13} aria-hidden="true" />{" "}
            {inferring ? "识别中…" : pendingCount ? `AI 识别（${pendingCount}）` : "AI 识别"}
          </button>
          <button className="link-btn" onClick={toggleSources}>
            {showSources ? "收起数据源 ▾" : "数据源 ▸"}
          </button>
        </div>
      )}

      {showSources && metrics.length > 0 && (
        <div className="metric-source-panel">
          {metrics.map((m) => (
            <div className="metric-row" key={m.key}>
              <span className="metric-source-name">{m.name}</span>
              <select
                className="text-input date"
                value={m.source ?? "manual"}
                onChange={(e) => setSource(m.key, e.target.value)}
              >
                {optionsFor(m.source).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}

      {err && (
        <p className="hint" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}
