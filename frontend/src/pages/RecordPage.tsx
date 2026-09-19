import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  ClockCounterClockwiseIcon,
  LightbulbIcon,
  GaugeIcon,
  SlidersHorizontalIcon,
  TrashIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import { api } from "../lib/api";
import type { EventItem, EventTypeItem, RecordStats } from "../lib/api";
import { BEHAVIOR_CATEGORIES } from "../lib/constants";
import { ICON_CHOICES } from "../lib/icons";
import {
  refreshEventTypes,
  useEventTypeResolver,
  useEventTypes,
} from "../lib/eventTypes";

interface FormState {
  energy: number;
  focus: number;
  irritation: number;
  showState: boolean;
  thought: string;
}

function parseTags(raw: string): string[] {
  return raw
    .split(/[,，\s]+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 12);
}

function toLocalInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

/** 记录按钮网格：主表单与补记表单共用，避免两处重复维护。 */
function TypeButtons({
  types,
  onPick,
  selected,
  mini,
  disabled,
}: {
  types: EventTypeItem[];
  onPick: (key: string) => void;
  selected?: string;
  mini?: boolean;
  disabled?: boolean;
}) {
  const { icon: typeIcon } = useEventTypeResolver();
  return (
    <div className={mini ? "backfill-grid" : "event-grid"}>
      {types.map((t, i) => {
        const EventIcon = typeIcon(t.key);
        return (
          <button
            key={t.key}
            className={
              (mini ? "event-btn mini" : "event-btn") +
              (selected === t.key ? " selected" : "")
            }
            disabled={disabled}
            onClick={() => onPick(t.key)}
            title={mini ? undefined : `快捷键 ${i + 1}`}
          >
            <EventIcon
              className={mini ? undefined : "event-icon"}
              size={mini ? 16 : 26}
              weight="duotone"
              aria-hidden="true"
            />
            <span>{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** 类型管理（内联面板，不引入 modal）：新建自定义类型 + 改名/改类/归档。 */
function TypeManager({
  types,
  onError,
}: {
  types: EventTypeItem[];
  onError: (msg: string) => void;
}) {
  const { icon: typeIcon } = useEventTypeResolver();
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState("other");
  const [icon, setIcon] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingLabel, setEditingLabel] = useState("");

  const custom = types.filter((t) => !t.builtin);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      // 刷新注册表：所有订阅者（含本面板与记录按钮）一起重渲染
      await refreshEventTypes();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="type-manager expand-enter">
      <div className="tm-new">
        <input
          className="text-input"
          placeholder="新类型名称，如「冥想」"
          maxLength={16}
          value={label}
          disabled={busy}
          onChange={(e) => setLabel(e.target.value)}
        />
        <select
          className="text-input"
          value={category}
          disabled={busy}
          aria-label="行为大类"
          onChange={(e) => setCategory(e.target.value)}
        >
          {BEHAVIOR_CATEGORIES.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
        <button
          className="primary small"
          disabled={busy || !label.trim()}
          onClick={() =>
            run(async () => {
              await api.createEventType({
                label: label.trim(),
                category,
                icon,
              });
              setLabel("");
              setIcon(null);
            })
          }
        >
          添加
        </button>
      </div>

      <div className="tm-icons" role="group" aria-label="选择图标">
        {ICON_CHOICES.map((choice) => (
          <button
            key={choice.key}
            className={"tm-icon" + (icon === choice.key ? " selected" : "")}
            title={choice.label}
            aria-label={choice.label}
            aria-pressed={icon === choice.key}
            disabled={busy}
            onClick={() => setIcon(icon === choice.key ? null : choice.key)}
          >
            <choice.Icon size={17} weight="duotone" aria-hidden="true" />
          </button>
        ))}
      </div>
      <p className="hint">图标可不选，默认按所选大类给一个</p>

      {custom.length > 0 && (
        <ul className="tm-list">
          {custom.map((t) => {
            const CustomIcon = typeIcon(t.key);
            return (
              <li key={t.key}>
                <CustomIcon size={16} weight="duotone" aria-hidden="true" />
                {editingId === t.id ? (
                  <input
                    autoFocus
                    className="text-input"
                    maxLength={16}
                    value={editingLabel}
                    disabled={busy}
                    onChange={(e) => setEditingLabel(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") setEditingId(null);
                      if (e.key === "Enter" && editingLabel.trim()) {
                        run(async () => {
                          await api.updateEventType(t.id!, {
                            label: editingLabel.trim(),
                          });
                          setEditingId(null);
                        });
                      }
                    }}
                  />
                ) : (
                  <span className="tm-label">{t.label}</span>
                )}
                <select
                  className="text-input"
                  value={t.category}
                  disabled={busy}
                  aria-label={`${t.label} 的行为大类`}
                  onChange={(e) =>
                    run(() =>
                      api.updateEventType(t.id!, { category: e.target.value }),
                    )
                  }
                >
                  {BEHAVIOR_CATEGORIES.map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.label}
                    </option>
                  ))}
                </select>
                {editingId === t.id ? (
                  <button
                    className="link-btn"
                    disabled={busy}
                    onClick={() => {
                      if (editingLabel.trim()) {
                        run(async () => {
                          await api.updateEventType(t.id!, {
                            label: editingLabel.trim(),
                          });
                          setEditingId(null);
                        });
                      } else {
                        setEditingId(null);
                      }
                    }}
                  >
                    保存
                  </button>
                ) : (
                  <button
                    className="link-btn"
                    disabled={busy}
                    onClick={() => {
                      setEditingId(t.id);
                      setEditingLabel(t.label);
                    }}
                  >
                    改名
                  </button>
                )}
                <button
                  className="link-btn danger-link"
                  disabled={busy}
                  title="归档后不再出现在记录按钮里，历史记录不受影响"
                  onClick={() => run(() => api.archiveEventType(t.id!))}
                >
                  <TrashIcon size={13} aria-hidden="true" /> 归档
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="hint">
        归档只是从按钮里收起；已记录的历史仍显示原来的名称与图标。
        {custom.length === 0 && "（还没有自定义类型）"}
      </p>
    </div>
  );
}

export default function RecordPage() {
  const types = useEventTypes();
  const { label: typeLabel, icon: typeIcon } = useEventTypeResolver();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [showManager, setShowManager] = useState(false);
  const [state, setState] = useState<FormState>({
    energy: 3,
    focus: 3,
    irritation: 0,
    showState: false,
    thought: "",
  });
  // 最近一次记录：记录后出现内联条，可就地补备注/加标签/撤销
  const [lastEvent, setLastEvent] = useState<EventItem | null>(null);
  const [editMode, setEditMode] = useState<null | "note" | "tags">(null);
  const [editValue, setEditValue] = useState("");
  // 即时回报（今天条数 + 连续天数）与今天的记录（最近在前）。
  // 「连续天数」没法在客户端推算 → 一律以后端为准，不在本地近似。
  const [stats, setStats] = useState<RecordStats | null>(null);
  const [todayEvents, setTodayEvents] = useState<EventItem[]>([]);
  // 补记表单
  const [showBackfill, setShowBackfill] = useState(false);
  const [backfillType, setBackfillType] = useState<string>(types[0]?.key ?? "");
  const [backfillWhen, setBackfillWhen] = useState<string>(() =>
    toLocalInputValue(new Date()),
  );
  const [backfillNote, setBackfillNote] = useState("");
  // 类型清单到达后，若选中的是被归档/删除的类型，回落到第一个可选类型
  useEffect(() => {
    const keys = new Set(types.map((t) => t.key));
    if (types.length === 0) return;
    if (!keys.has(backfillType)) setBackfillType(types[0].key);
  }, [types, backfillType]);

  useEffect(() => {
    document.title = "LifeLab · 记录";
  }, []);

  // 回报拉不动不该挡住记录本身 —— 静默失败，计数器只是锦上添花
  const refreshStats = useCallback(() => {
    api.recordStats().then(setStats).catch(() => {});
  }, []);

  const refreshToday = useCallback(() => {
    api.listEvents().then(setTodayEvents).catch(() => {});
  }, []);

  useEffect(() => {
    refreshStats();
    refreshToday();
  }, [refreshStats, refreshToday]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2000);
  }

  const record = useCallback(
    async (type: string) => {
      setBusy(true);
      setError(null);
      try {
        const created = await api.createEvent(type);
        setLastEvent(created);
        // 本地插入，让「最近记录」立刻跟上，不等一次往返
        setTodayEvents((prev) => [created, ...prev]);
        setEditMode(null);
        setEditValue("");
        refreshStats();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [refreshStats],
  );

  // 内联条 5 秒后自动收起；正在编辑时不收
  useEffect(() => {
    if (!lastEvent || editMode) return;
    const timer = setTimeout(() => setLastEvent(null), 5000);
    return () => clearTimeout(timer);
  }, [lastEvent, editMode]);

  // 数字键 1~N 快速记录（输入框内不触发）
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const el = document.activeElement;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      const n = Number(e.key);
      if (Number.isInteger(n) && n >= 1 && n <= types.length) {
        record(types[n - 1].key);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [record, types]);

  async function saveEdit() {
    if (!lastEvent || !editMode) return;
    setBusy(true);
    setError(null);
    try {
      const patch =
        editMode === "note"
          ? { note: editValue.trim() || null }
          : { tags: parseTags(editValue) };
      const updated = await api.updateEvent(lastEvent.id, patch);
      setLastEvent(updated);
      setEditMode(null);
      setEditValue("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function undoLast() {
    if (!lastEvent) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEvent(lastEvent.id);
      setTodayEvents((prev) => prev.filter((e) => e.id !== lastEvent.id));
      setLastEvent(null);
      setEditMode(null);
      refreshStats();
      flash("已撤销");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveThought() {
    if (!state.thought.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createThought(state.thought, []);
      setState((s) => ({ ...s, thought: "" }));
      flash("已保存想法");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveState() {
    setBusy(true);
    setError(null);
    try {
      await api.createState({
        energy: state.energy,
        focus: state.focus,
        irritation: state.irritation,
      });
      setState((s) => ({ ...s, showState: false }));
      flash("已记录状态");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveBackfill() {
    if (!backfillWhen) return;
    setBusy(true);
    setError(null);
    try {
      // datetime-local 是本地时间 → 转成带时区 ISO（浏览器本地偏移）
      const local = new Date(backfillWhen);
      if (Number.isNaN(local.getTime())) throw new Error("时间格式无效");
      await api.createEvent(backfillType, backfillNote || undefined, local.toISOString());
      setBackfillNote("");
      setShowBackfill(false);
      flash("已补记事件");
      // 补记的时间戳不一定是今天 → 回报与「最近记录」都重新拉一次
      refreshStats();
      refreshToday();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // 今天记过的类型，按最近一次出现排序。生活单调时的主要动作就是重复其中某一个，
  // 所以把它们提到类型网格之前，点一下就再来一条。
  const recentTypes: string[] = [];
  for (const e of todayEvents) {
    if (!recentTypes.includes(e.type)) recentTypes.push(e.type);
    if (recentTypes.length === 3) break;
  }

  return (
    <div className="page">
      <section className="card">
        <h2>当前在做什么？</h2>
        <p className="hint">
          点一下就记下（也可按数字键）；只记设备看不到的事，在线使用由采集自动生成
        </p>
        {stats && (
          <p className="record-stats">
            {stats.today_count > 0 ? (
              <>
                今天已记 <strong>{stats.today_count}</strong> 条
                {stats.streak_days > 1 && (
                  <>
                    {" · "}连续 <strong>{stats.streak_days}</strong> 天
                  </>
                )}
              </>
            ) : (
              <>今天还没记录</>
            )}
          </p>
        )}
        {recentTypes.length > 0 && (
          <div className="recent-row">
            <span className="recent-label">最近记录</span>
            {recentTypes.map((type) => {
              const RecentIcon = typeIcon(type);
              return (
                <button
                  key={type}
                  className="recent-chip"
                  disabled={busy}
                  onClick={() => record(type)}
                  title={`再记一条「${typeLabel(type)}」`}
                >
                  <RecentIcon size={14} weight="duotone" aria-hidden="true" />
                  {typeLabel(type)}
                </button>
              );
            })}
          </div>
        )}
        <TypeButtons types={types} onPick={record} disabled={busy} />

        {lastEvent && (
          <div className="record-bar" role="status">
            <span className="record-bar-text">
              已记录「{typeLabel(lastEvent.type)}」
              {lastEvent.note && (
                <em className="record-bar-extra">{lastEvent.note}</em>
              )}
              {lastEvent.tags.length > 0 && (
                <em className="record-bar-extra">
                  {lastEvent.tags.map((t) => `#${t}`).join(" ")}
                </em>
              )}
            </span>
            {editMode === null ? (
              <span className="record-bar-actions">
                <button
                  className="link-btn"
                  disabled={busy}
                  onClick={() => record(lastEvent.type)}
                >
                  再记一次
                </button>
                <button
                  className="link-btn"
                  disabled={busy}
                  onClick={() => {
                    setEditMode("note");
                    setEditValue(lastEvent.note ?? "");
                  }}
                >
                  补备注
                </button>
                <button
                  className="link-btn"
                  disabled={busy}
                  onClick={() => {
                    setEditMode("tags");
                    setEditValue(lastEvent.tags.join(","));
                  }}
                >
                  加标签
                </button>
                <button
                  className="link-btn danger-link"
                  disabled={busy}
                  onClick={undoLast}
                >
                  撤销
                </button>
              </span>
            ) : (
              <span className="record-bar-edit">
                <input
                  autoFocus
                  className="text-input"
                  placeholder={
                    editMode === "note" ? "备注" : "标签，逗号分隔"
                  }
                  value={editValue}
                  disabled={busy}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveEdit();
                    if (e.key === "Escape") {
                      setEditMode(null);
                      setEditValue("");
                    }
                  }}
                  aria-label={editMode === "note" ? "备注" : "标签"}
                />
                <button
                  className="secondary small"
                  disabled={busy}
                  onClick={saveEdit}
                >
                  保存
                </button>
                <button
                  className="link-btn"
                  disabled={busy}
                  onClick={() => {
                    setEditMode(null);
                    setEditValue("");
                  }}
                >
                  取消
                </button>
              </span>
            )}
          </div>
        )}

        <div className="backfill-toggle">
          <button
            className="link-btn"
            onClick={() => setShowManager((s) => !s)}
          >
            <SlidersHorizontalIcon size={14} aria-hidden="true" />{" "}
            {showManager ? "收起类型管理" : "管理类型"}
          </button>
          <button
            className="link-btn"
            onClick={() => setShowBackfill((s) => !s)}
          >
            <ClockCounterClockwiseIcon size={14} aria-hidden="true" />{" "}
            {showBackfill ? "收起补记" : "补记 / 预记事件（选时间）"}
          </button>
        </div>
        {showManager && <TypeManager types={types} onError={setError} />}
        {showBackfill && (
          <div className="backfill-form expand-enter">
            <TypeButtons
              types={types}
              mini
              selected={backfillType}
              onPick={setBackfillType}
            />
            <div className="backfill-row">
              <input
                type="datetime-local"
                className="text-input date"
                value={backfillWhen}
                max={toLocalInputValue(
                  new Date(Date.now() + 30 * 24 * 3600 * 1000),
                )}
                min={toLocalInputValue(
                  new Date(Date.now() - 30 * 24 * 3600 * 1000),
                )}
                onChange={(e) => setBackfillWhen(e.target.value)}
              />
              <input
                className="text-input"
                placeholder="备注（可选）"
                value={backfillNote}
                onChange={(e) => setBackfillNote(e.target.value)}
              />
            </div>
            <button
              className="primary"
              disabled={busy || !backfillWhen}
              onClick={saveBackfill}
            >
              保存到所选时间
            </button>
            <p className="hint">仅限 ±30 天，时间精确到分钟；漏记/想预安排都可用</p>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <h2>
            <LightbulbIcon size={17} weight="duotone" aria-hidden="true" /> 记下一个想法
          </h2>
        </div>
        <textarea
          className="text-input tall"
          placeholder="此刻在想什么？为什么？"
          value={state.thought}
          onChange={(e) => setState((s) => ({ ...s, thought: e.target.value }))}
        />
        <button className="primary" disabled={busy || !state.thought.trim()} onClick={saveThought}>
          保存想法
        </button>
      </section>

      <section className="card">
        <div className="card-header">
          <h2>
            <GaugeIcon size={17} weight="duotone" aria-hidden="true" /> 当前状态
          </h2>
          <button
            className="link-btn"
            onClick={() => setState((s) => ({ ...s, showState: !s.showState }))}
          >
            {state.showState ? "收起" : "打分"}
          </button>
        </div>
        {state.showState && (
          <div className="state-form expand-enter">
            {(
              [
                ["energy", "精力"],
                ["focus", "注意力"],
                ["irritation", "烦躁"],
              ] as const
            ).map(([key, label]) => (
              <div className="slider-row" key={key}>
                <span className="slider-label">{label}</span>
                <input
                  type="range"
                  min={0}
                  max={5}
                  value={state[key]}
                  style={{ "--fill": `${(state[key] / 5) * 100}%` } as CSSProperties}
                  aria-label={`${label}（0-5 分）`}
                  onChange={(e) =>
                    setState((s) => ({ ...s, [key]: Number(e.target.value) }))
                  }
                />
                <span className="slider-value">{state[key]}</span>
              </div>
            ))}
            <button className="primary" disabled={busy} onClick={saveState}>
              保存状态
            </button>
          </div>
        )}
      </section>

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          <WarningIcon size={14} aria-hidden="true" /> {error}（点击关闭）
        </div>
      )}
      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </div>
  );
}
