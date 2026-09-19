import { useCallback, useEffect, useState } from "react";
import {
  ArrowCounterClockwiseIcon,
  ArrowDownIcon,
  ArrowRightIcon,
  ArrowUpIcon,
  ChartLineUpIcon,
  CheckCircleIcon,
  ClockCounterClockwiseIcon,
  FlagIcon,
  FlaskIcon,
  HourglassMediumIcon,
  PauseIcon,
  PushPinIcon,
  QuestionIcon,
  RulerIcon,
  ScrollIcon,
  TrashIcon,
  WarningIcon,
  XCircleIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { api } from "../../lib/api";
import type {
  ExperimentDetail,
  ExperimentStatusChange,
  ExperimentStatus,
  MetricDef,
  Verdict,
} from "../../lib/api";
import { STATUS_LABELS } from "../../lib/constants";
import MetricEditor from "./MetricEditor";
import Sparkline from "./Sparkline";

const VERDICT_META: Record<Verdict, { label: string; icon: Icon; tone: string }> = {
  SUPPORTED: { label: "支持假设", icon: CheckCircleIcon, tone: "good" },
  PARTIALLY_SUPPORTED: { label: "部分支持", icon: HourglassMediumIcon, tone: "warn" },
  REFUTED: { label: "推翻假设", icon: XCircleIcon, tone: "bad" },
  INCONCLUSIVE: { label: "不确定", icon: QuestionIcon, tone: "muted" },
};

const TREND_ICON: Record<string, Icon> = {
  up: ArrowUpIcon,
  down: ArrowDownIcon,
  flat: ArrowRightIcon,
};

// 前进迁移（真实过程，写状态历史）
const nextTransitions: Record<ExperimentStatus, ExperimentStatus[]> = {
  DRAFT: ["RUNNING"],
  RUNNING: ["PAUSED", "COMPLETED"],
  PAUSED: ["RUNNING", "COMPLETED"],
  COMPLETED: ["CONCLUDED"],
  CONCLUDED: [],
};

// 回退（误触保底，撤销最后一步，不写历史）
const REVERT_TARGET: Partial<Record<ExperimentStatus, ExperimentStatus>> = {
  CONCLUDED: "COMPLETED",
  COMPLETED: "RUNNING",
};

const REVERT_LABELS: Record<string, string> = {
  COMPLETED: "撤销结论（回到已完成）",
  RUNNING: "撤销完成（回到进行中）",
};

function toLocalInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function fmtDate(ts: string | null): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDays(v: number | null): string {
  if (v === null || v === undefined) return "—";
  if (v < 0.01) return "<0.01 天";
  return `${v.toFixed(1)} 天`;
}

type TransitionMode = "pause" | "complete" | "conclude" | null;

export default function ExperimentCard({
  expId,
  onChanged,
  onDeleted,
}: {
  expId: number;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [exp, setExp] = useState<ExperimentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<TransitionMode>(null);
  const [problemId, setProblemId] = useState<number | null>(null);

  // 状态迁移表单
  const [reason, setReason] = useState("");
  const [analysis, setAnalysis] = useState("");
  const [verdict, setVerdict] = useState<Verdict>("SUPPORTED");
  const [conclusion, setConclusion] = useState("");
  const [conclusionReason, setConclusionReason] = useState("");
  const [confidence, setConfidence] = useState(0.6);
  const [writeFinding, setWriteFinding] = useState(false);

  // 数据点录入
  const [logMetric, setLogMetric] = useState("");
  const [logValue, setLogValue] = useState("");
  const [logNote, setLogNote] = useState("");
  const [logWhen, setLogWhen] = useState(() => toLocalInputValue(new Date()));

  // 编辑指标
  const [editingMetrics, setEditingMetrics] = useState(false);
  const [metricsDraft, setMetricsDraft] = useState<MetricDef[]>([]);
  const [inferKeys, setInferKeys] = useState<string[]>([]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const detail = await api.experimentDetail(expId);
      setExp(detail);
      const firstManual = (detail.metrics ?? []).find(
        (m) => !m.source || m.source === "manual",
      );
      setLogMetric((cur) => cur || firstManual?.key || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [expId]);

  useEffect(() => {
    load();
  }, [load]);

  function resetForms() {
    setMode(null);
    setReason("");
    setAnalysis("");
    setConclusion("");
    setConclusionReason("");
    setConfidence(0.6);
    setWriteFinding(false);
  }

  async function transition(payload: ExperimentStatusChange) {
    setBusy(true);
    setError(null);
    try {
      await api.setExperimentStatus(expId, payload);
      resetForms();
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function revert() {
    if (!exp) return;
    const target = REVERT_TARGET[exp.status];
    if (!target) return;
    const msg =
      exp.status === "CONCLUDED"
        ? "撤销结论？将回到「已完成」，结论字段会被清除（完成与效果分析保留）。"
        : "撤销完成？将回到「进行中」，结束时间与效果分析会被清除，之前的数据保留。";
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    try {
      await api.revertExperiment(expId);
      resetForms();
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function conclude() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.concludeExperiment(expId, {
        status: "CONCLUDED",
        verdict,
        conclusion: conclusion.trim(),
        conclusion_reason: conclusionReason.trim(),
        conclusion_confidence: confidence,
        write_finding: writeFinding,
        problem_id: problemId ?? undefined,
      });
      resetForms();
      await load();
      onChanged();
      if (r.finding_id) {
        setError(null);
        window.alert(`已下结论，并写入知识库 Finding #${r.finding_id}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addLog() {
    const value = Number(logValue);
    if (!logMetric || logValue.trim() === "" || Number.isNaN(value)) return;
    setBusy(true);
    setError(null);
    try {
      await api.createExperimentLog(expId, {
        metric: logMetric,
        value,
        note: logNote.trim() || undefined,
        timestamp: logWhen ? new Date(logWhen).toISOString() : undefined,
      });
      setLogValue("");
      setLogNote("");
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeLog(logId: number) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteExperimentLog(expId, logId);
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function aggregate() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.aggregateExperiment(expId);
      await load();
      onChanged();
      setNote(
        r.created + r.updated + r.deleted === 0
          ? "没有可聚合的自动指标或运行期间无数据。"
          : `聚合完成：新增 ${r.created}、更新 ${r.updated}、清除 ${r.deleted}（覆盖 ${r.days} 天）`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveMetrics() {
    setBusy(true);
    setError(null);
    try {
      await api.updateExperiment(expId, { metrics: metricsDraft, infer_keys: inferKeys });
      const r = await api.aggregateExperiment(expId);
      setEditingMetrics(false);
      await load();
      onChanged();
      setNote(
        r.created + r.updated + r.deleted === 0
          ? "指标已更新。新指标暂无可用数据（如手机分小时需重装 APK 后才有）。"
          : `指标已更新并重算数据点：新增 ${r.created}、更新 ${r.updated}、清除 ${r.deleted}（覆盖 ${r.days} 天）`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!exp) return;
    if (!window.confirm(`确定删除实验「${exp.name}」？其数据点与历史都会删除。`)) return;
    setBusy(true);
    try {
      await api.deleteExperiment(expId);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  if (!exp) {
    return (
      <section className="card">
        {error ? (
          <div className="error" role="alert">
            <WarningIcon size={14} aria-hidden="true" /> {error}
          </div>
        ) : (
          <p className="empty">加载中…</p>
        )}
      </section>
    );
  }

  const metricDefs: MetricDef[] = exp.metrics ?? [];
  const manualMetrics = metricDefs.filter((m) => !m.source || m.source === "manual");
  const hasAutoMetrics = metricDefs.some((m) => m.source && m.source !== "manual");

  // 未定义指标时仍允许手动输入自定义指标名
  const hasCustomEntry = metricDefs.length === 0;

  const metricLabel = (key: string): string => {
    const def = metricDefs.find((m) => m.key === key);
    return def ? `${def.name}${def.unit ? `（${def.unit}）` : ""}` : key;
  };

  /** 数据点展示精度：自动聚合会摊出 17516.666666666668 这种长小数，
   * 按指标语义收一下 —— 时长/次数取整，其余最多一位小数。 */
  const metricValue = (key: string, v: number): string => {
    const def = metricDefs.find((m) => m.key === key);
    const src = def?.source ?? "manual";
    const unit = def?.unit ?? "";
    const isCount = src.startsWith("event_count");
    const isDuration =
      /^(usage_|event_duration)/.test(src) || /分钟|小时|秒|min|hour|sec/i.test(unit);
    if (isCount || isDuration) return String(Math.round(v));
    return Number.isInteger(v) ? String(v) : v.toFixed(1);
  };

  return (
    <section className="card experiment">
      <div className="exp-header">
        <div>
          <span className={`status status-${exp.status.toLowerCase()}`}>
            {STATUS_LABELS[exp.status]}
          </span>
          <span className="exp-name">{exp.name}</span>
        </div>
        <div className="ai-actions">
          <button className="danger small" disabled={busy} onClick={remove}>
            删除
          </button>
        </div>
      </div>

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          <WarningIcon size={14} aria-hidden="true" /> {error}（点击关闭）
        </div>
      )}

      <div className="exp-body">
        {/* 设计 */}
        <div className="exp-block">
          <div className="exp-block-title">
            <FlaskIcon size={14} aria-hidden="true" /> 实验设计
          </div>
          {exp.question && (
            <p>
              <b>问题：</b>
              {exp.question}
            </p>
          )}
          {exp.hypothesis && (
            <p>
              <b>假设：</b>
              {exp.hypothesis}
            </p>
          )}
          {exp.variable && (
            <p>
              <b>变量：</b>
              {exp.variable}
            </p>
          )}
          {!exp.variable && exp.indicator && (
            <p>
              <b>指标（文本）：</b>
              {exp.indicator}
            </p>
          )}
          {editingMetrics ? (
            <>
              <MetricEditor
                metrics={metricsDraft}
                onChange={setMetricsDraft}
                onInferKeysChange={setInferKeys}
                initialResolved={(exp.metrics ?? []).map((m) => m.key)}
              />
              <div className="ai-actions">
                <button className="primary small" disabled={busy} onClick={saveMetrics}>
                  保存指标
                </button>
                <button
                  className="secondary small"
                  disabled={busy}
                  onClick={() => setEditingMetrics(false)}
                >
                  取消
                </button>
              </div>
              <p className="hint">
                保存后立即重算自动数据点；新增的手动指标需自行录入，删除的指标其自动数据点一并清除。
              </p>
            </>
          ) : (
            <>
              {metricDefs.length > 0 && (
                <p>
                  <b>结构化指标：</b>
                  {metricDefs.map((m) => (
                    <span className="metric-chip" key={m.key}>
                      {m.name}
                      {m.unit ? `(${m.unit})` : ""}
                      {m.direction === "up_good" ? " 越高越好" : m.direction === "down_good" ? " 越低越好" : ""}
                      {m.source && m.source !== "manual" && (
                        <span className="metric-auto"> · 自动</span>
                      )}
                    </span>
                  ))}
                </p>
              )}
              <button
                className="secondary small"
                disabled={busy}
                onClick={() => {
                  setMetricsDraft(exp.metrics ?? []);
                  setEditingMetrics(true);
                }}
              >
                <RulerIcon size={13} aria-hidden="true" /> 编辑指标
              </button>
            </>
          )}
          {exp.expected_days && (
            <p className="muted">预计周期：{exp.expected_days} 天</p>
          )}
          {exp.baseline_note && (
            <p className="muted">基线：{exp.baseline_note}</p>
          )}
          <p className="muted">
            开始：{fmtDate(exp.started_at)} · 结束：{fmtDate(exp.ended_at)}
          </p>
          {exp.stats && (
            <p className="muted">
              有效运行 {fmtDays(exp.stats.running_days)} · 累计暂停{" "}
              {fmtDays(exp.stats.paused_total_days)} · 时间跨度{" "}
              {fmtDays(exp.stats.days_elapsed)}
            </p>
          )}
        </div>

        {/* 数据 */}
        <div className="exp-block">
          <div className="exp-block-title">
            <ChartLineUpIcon size={14} aria-hidden="true" /> 数据记录（
            {exp.stats?.total_logs ?? 0} 点）
          </div>
          {note && <p className="hint sync-msg">{note}</p>}

          {hasAutoMetrics && (
            <div className="agg-row">
              <span className="hint">自动指标从你的日常记录按天聚合</span>
              <button className="secondary small" disabled={busy} onClick={aggregate}>
                {busy ? (
                  "聚合中…"
                ) : (
                  <>
                    <ArrowCounterClockwiseIcon size={13} aria-hidden="true" /> 立即聚合
                  </>
                )}
              </button>
            </div>
          )}

          {/* 手动录入：存在手动指标，或未定义指标时允许自定义指标名 */}
          {(manualMetrics.length > 0 || hasCustomEntry) && (
            <div className="log-form">
              {manualMetrics.length > 0 ? (
                <select
                  className="text-input date"
                  value={logMetric}
                  onChange={(e) => setLogMetric(e.target.value)}
                >
                  {manualMetrics.map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.name}
                      {m.unit ? `(${m.unit})` : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  className="text-input"
                  placeholder="指标名"
                  value={logMetric}
                  onChange={(e) => setLogMetric(e.target.value)}
                />
              )}
              <input
                className="text-input narrow"
                placeholder="数值"
                type="number"
                step="any"
                value={logValue}
                onChange={(e) => setLogValue(e.target.value)}
              />
              <input
                type="datetime-local"
                className="text-input date"
                value={logWhen}
                onChange={(e) => setLogWhen(e.target.value)}
              />
              <input
                className="text-input"
                placeholder="备注（可选）"
                value={logNote}
                onChange={(e) => setLogNote(e.target.value)}
              />
              <button
                className="primary small"
                disabled={busy || !logMetric || logValue.trim() === ""}
                onClick={addLog}
              >
                记录
              </button>
            </div>
          )}

          {metricDefs.length === 0 && (
            <p className="hint">
              未定义结构化指标。点上方「编辑指标」可配置自动数据源（如学习时长/日均专注）。
            </p>
          )}

          {exp.stats && exp.stats.metric_stats.length > 0 && (
            <div className="stats-table">
              {exp.stats.metric_stats.map((m) => (
                <div className="stat-row-metric" key={m.metric}>
                  <span className="stat-metric-name">{metricLabel(m.metric)}</span>
                  <span className="stat-metric-num">
                    n={m.count} · 均值 {m.mean?.toFixed(1)} · {m.min?.toFixed(1)}~
                    {m.max?.toFixed(1)}
                  </span>
                  <span
                    className={
                      "stat-trend" +
                      (m.improved === true
                        ? " good"
                        : m.improved === false
                          ? " bad"
                          : "")
                    }
                  >
                    {(() => {
                      const TrendIcon = TREND_ICON[m.trend] ?? ArrowRightIcon;
                      return <TrendIcon size={13} aria-hidden="true" />;
                    })()}
                    {m.change !== null ? ` ${m.change > 0 ? "+" : ""}${m.change.toFixed(1)}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* 数据点列表 + 图（简单展示最近记录） */}
          <LogListView
            expId={expId}
            busy={busy}
            onDelete={removeLog}
            metricLabel={metricLabel}
            metricValue={metricValue}
          />
        </div>

        {/* 状态时间轴 */}
        <div className="exp-block">
          <div className="exp-block-title">
            <ClockCounterClockwiseIcon size={14} aria-hidden="true" /> 状态历史
          </div>
          {exp.status_events.length === 0 && <p className="hint">尚无状态变更。</p>}
          {exp.status_events.map((ev) => (
            <div className="timeline-row" key={ev.id}>
              <span className="muted">{fmtDate(ev.created_at)}</span>
              <span>
                {ev.from_status ? STATUS_LABELS[ev.from_status] : "创建"}{" "}
                <ArrowRightIcon size={11} aria-hidden="true" />{" "}
                <b>{STATUS_LABELS[ev.to_status]}</b>
              </span>
              {ev.reason && <span className="timeline-reason">{ev.reason}</span>}
            </div>
          ))}
        </div>

        {/* 效果分析 / 结论 */}
        {(exp.completion_analysis || exp.conclusion) && (
          <div className="exp-block">
            <div className="exp-block-title">
              <ScrollIcon size={14} aria-hidden="true" /> 分析 & 结论
            </div>
            {exp.completion_analysis && (
              <p>
                <b>效果分析：</b>
                {exp.completion_analysis}
              </p>
            )}
            {exp.result_verdict &&
              (() => {
                const v = VERDICT_META[exp.result_verdict];
                const VerdictIcon = v.icon;
                return (
                  <p className={`verdict verdict-${v.tone}`}>
                    <b>判定：</b>
                    <VerdictIcon size={14} aria-hidden="true" /> {v.label}
                    {exp.conclusion_confidence !== null &&
                      `（置信度 ${Math.round(exp.conclusion_confidence * 100)}%）`}
                  </p>
                );
              })()}
            {exp.conclusion && (
              <p>
                <b>结论：</b>
                {exp.conclusion}
              </p>
            )}
            {exp.conclusion_reason && (
              <p className="finding-interp">
                <b>依据：</b>
                {exp.conclusion_reason}
              </p>
            )}
          </div>
        )}

        {/* 状态操作：前进（写历史）+ 回退（撤销误触，不写历史） */}
        <div className="exp-actions">
          {nextTransitions[exp.status].map((t) => {
            if (t === "PAUSED") {
              return (
                <button
                  key={t}
                  className="secondary small"
                  disabled={busy}
                  onClick={() => setMode(mode === "pause" ? null : "pause")}
                >
                  <PauseIcon size={13} aria-hidden="true" /> 暂停
                </button>
              );
            }
            if (t === "COMPLETED") {
              return (
                <button
                  key={t}
                  className="primary small"
                  disabled={busy}
                  onClick={() => setMode(mode === "complete" ? null : "complete")}
                >
                  <FlagIcon size={13} aria-hidden="true" /> 完成实验
                </button>
              );
            }
            if (t === "CONCLUDED") {
              return (
                <button
                  key={t}
                  className="primary small"
                  disabled={busy}
                  onClick={() => setMode(mode === "conclude" ? null : "conclude")}
                >
                  <PushPinIcon size={13} aria-hidden="true" /> 下结论
                </button>
              );
            }
            return (
              <button
                key={t}
                className="primary small"
                disabled={busy}
                onClick={() => transition({ status: t })}
              >
                {STATUS_LABELS[t]}
              </button>
            );
          })}
          {REVERT_TARGET[exp.status] && (
            <button
              className="secondary small revert-btn"
              disabled={busy}
              title="误触保底：撤销最后一步，不写入状态历史"
              onClick={() => revert()}
            >
              <ArrowCounterClockwiseIcon size={13} aria-hidden="true" />{" "}
              {REVERT_LABELS[REVERT_TARGET[exp.status]!]}
            </button>
          )}
        </div>
        {REVERT_TARGET[exp.status] && (
          <p className="hint">
            回退仅用于撤销误触（不记入状态历史）；撤销完成后可重新完成/下结论。
          </p>
        )}

        {/* 暂停表单 */}
        {mode === "pause" && (
          <div className="transition-form expand-enter">
            <textarea
              className="text-input tall"
              placeholder="暂停原因 *（必填）：如「出差一周无法执行」"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <button
              className="primary"
              disabled={busy || !reason.trim()}
              onClick={() => transition({ status: "PAUSED", reason: reason.trim() })}
            >
              确认暂停
            </button>
          </div>
        )}

        {/* 完成表单 */}
        {mode === "complete" && (
          <div className="transition-form expand-enter">
            <textarea
              className="text-input tall"
              placeholder="效果分析 *（必填）：实验期间发生了什么、指标怎么变化、主观感受"
              value={analysis}
              onChange={(e) => setAnalysis(e.target.value)}
            />
            <button
              className="primary"
              disabled={busy || !analysis.trim()}
              onClick={() =>
                transition({ status: "COMPLETED", completion_analysis: analysis.trim() })
              }
            >
              确认完成
            </button>
          </div>
        )}

        {/* 结论表单 */}
        {mode === "conclude" && (
          <TransitionConclude
            busy={busy}
            verdict={verdict}
            setVerdict={setVerdict}
            conclusion={conclusion}
            setConclusion={setConclusion}
            conclusionReason={conclusionReason}
            setConclusionReason={setConclusionReason}
            confidence={confidence}
            setConfidence={setConfidence}
            writeFinding={writeFinding}
            setWriteFinding={setWriteFinding}
            problemId={problemId}
            setProblemId={setProblemId}
            onSubmit={conclude}
            onCancel={resetForms}
          />
        )}
      </div>
    </section>
  );
}

function LogListView({
  expId,
  busy,
  onDelete,
  metricLabel,
  metricValue,
}: {
  expId: number;
  busy: boolean;
  onDelete: (id: number) => void;
  metricLabel: (k: string) => string;
  metricValue: (k: string, v: number) => string;
}) {
  const [logs, setLogs] = useState<import("../../lib/api").ExperimentLog[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [sparkMetric, setSparkMetric] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLogs(await api.experimentLogs(expId));
    } catch {
      /* 父组件已有错误处理 */
    }
  }, [expId]);

  useEffect(() => {
    load();
  }, [load]);

  const shown = showAll ? logs : logs.slice(0, 8);
  if (logs.length === 0) return null;

  // 可画图的指标（数据点 >= 2，按时间升序）
  const byMetric = new Map<string, number[]>();
  for (const log of [...logs].reverse()) {
    const arr = byMetric.get(log.metric) ?? [];
    arr.push(log.value);
    byMetric.set(log.metric, arr);
  }
  const sparkable = [...byMetric.entries()].filter(([, v]) => v.length >= 2);
  const active = sparkMetric ?? sparkable[0]?.[0] ?? null;
  const activeValues = active ? (byMetric.get(active) ?? []) : [];

  return (
    <div className="log-list">
      {sparkable.length > 0 && active && (
        <div className="spark-box">
          <div className="spark-head">
            <select
              className="text-input date"
              value={active}
              onChange={(e) => setSparkMetric(e.target.value)}
            >
              {sparkable.map(([k]) => (
                <option key={k} value={k}>
                  {metricLabel(k)}
                </option>
              ))}
            </select>
            <span className="muted">
              共 {activeValues.length} 点 · {metricValue(active, activeValues[0])} 至{" "}
              {metricValue(active, activeValues[activeValues.length - 1])}
            </span>
          </div>
          <Sparkline values={activeValues} width={280} height={56} />
        </div>
      )}
      {shown.map((log) => (
        <div className="log-row" key={log.id}>
          <span className="log-time">{fmtDate(log.timestamp)}</span>
          <span className="log-metric">{metricLabel(log.metric)}</span>
          <span className="log-value">{metricValue(log.metric, log.value)}</span>
          {log.note && <span className="log-note">{log.note}</span>}
          <button
            className="link-btn danger-link"
            disabled={busy}
            onClick={() => onDelete(log.id)}
            aria-label={`删除数据点：${metricLabel(log.metric)} ${metricValue(log.metric, log.value)}`}
          >
            <TrashIcon size={14} aria-hidden="true" />
          </button>
        </div>
      ))}
      {logs.length > 8 && (
        <button className="link-btn" onClick={() => setShowAll((s) => !s)}>
          {showAll ? "收起" : `展开全部 ${logs.length} 条`}
        </button>
      )}
    </div>
  );
}

function TransitionConclude({
  busy,
  verdict,
  setVerdict,
  conclusion,
  setConclusion,
  conclusionReason,
  setConclusionReason,
  confidence,
  setConfidence,
  writeFinding,
  setWriteFinding,
  problemId,
  setProblemId,
  onSubmit,
  onCancel,
}: {
  busy: boolean;
  verdict: Verdict;
  setVerdict: (v: Verdict) => void;
  conclusion: string;
  setConclusion: (v: string) => void;
  conclusionReason: string;
  setConclusionReason: (v: string) => void;
  confidence: number;
  setConfidence: (v: number) => void;
  writeFinding: boolean;
  setWriteFinding: (v: boolean) => void;
  problemId: number | null;
  setProblemId: (v: number | null) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const [problems, setProblems] = useState<{ id: number; title: string }[]>([]);

  useEffect(() => {
    api
      .problems()
      .then((ps) => setProblems(ps.map((p) => ({ id: p.id, title: p.title }))))
      .catch(() => {});
  }, []);

  const valid = conclusion.trim() && conclusionReason.trim();

  return (
    <div className="transition-form expand-enter">
      <label className="sr-only" htmlFor="verdict-select">
        结论判定
      </label>
      <select
        id="verdict-select"
        className="text-input date"
        value={verdict}
        onChange={(e) => setVerdict(e.target.value as Verdict)}
      >
        {(Object.keys(VERDICT_META) as Verdict[]).map((v) => (
          <option key={v} value={v}>
            {VERDICT_META[v].label}
          </option>
        ))}
      </select>
      <label className="sr-only" htmlFor="conclusion-text">
        结论文本
      </label>
      <textarea
        id="conclusion-text"
        className="text-input tall"
        placeholder="结论 *（必填）：一句话概括实验得到了什么"
        value={conclusion}
        onChange={(e) => setConclusion(e.target.value)}
      />
      <label className="sr-only" htmlFor="conclusion-reason">
        结论依据
      </label>
      <textarea
        id="conclusion-reason"
        className="text-input tall"
        placeholder="结论依据 *（必填）：哪些数据/观察支持这个结论"
        value={conclusionReason}
        onChange={(e) => setConclusionReason(e.target.value)}
      />
      <div className="slider-row">
        <span className="slider-label">置信度</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={confidence}
          onChange={(e) => setConfidence(Number(e.target.value))}
        />
        <span className="slider-value">{Math.round(confidence * 100)}%</span>
      </div>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={writeFinding}
          onChange={(e) => setWriteFinding(e.target.checked)}
        />
        同时写入知识库（经你授权才写入 Finding）
      </label>
      {writeFinding && (
        <select
          className="text-input date"
          aria-label="结论挂到哪个问题"
          value={problemId ?? ""}
          onChange={(e) => setProblemId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">不挂到具体问题</option>
          {problems.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </select>
      )}
      <div className="ai-actions">
        <button className="primary" disabled={busy || !valid} onClick={onSubmit}>
          确认下结论
        </button>
        <button className="secondary" disabled={busy} onClick={onCancel}>
          取消
        </button>
      </div>
    </div>
  );
}
