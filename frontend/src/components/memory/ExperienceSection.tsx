import { useCallback, useEffect, useMemo, useState } from "react";
import { api, today } from "../../lib/api";
import type {
  DeviceHour,
  EventSegment,
  FloatingItem,
  MemoryItem,
  StateItem,
  ThoughtItem,
  UnclassifiedApp,
  UsageOverviewDevice,
} from "../../lib/api";
import { BEHAVIOR_CATEGORIES, categoryLabel } from "../../lib/constants";
import { ICONS } from "../../lib/icons";
import { useEventTypeResolver } from "../../lib/eventTypes";

function fmtTime(ts: string): string {
  return new Date(ts).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function addDays(iso: string, days: number): string {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function toLocalInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function fmtDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} 分钟`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

function fmtSeconds(seconds: number): string {
  if (seconds <= 0) return "0 分钟";
  if (seconds < 60) return "<1 分钟";
  return fmtDuration(Math.round(seconds / 60));
}

const PLATFORM_LABELS: Record<string, string> = { pc: "电脑", android: "手机" };

// 设备观测到的活动不可能落「上床/吃饭」这类线下大类，所以规则只给在线大类
const ONLINE_CATEGORIES = BEHAVIOR_CATEGORIES.filter((c) => c.online);

export interface AppRuleSave {
  match_value: string;
  scope: "app" | "domain";
  category: string;
  display_label: string | null;
}

/** 给某个应用/站点就地建规则：显示名（可留空）+ 行为大类。 */
function AppRuleEditor({
  app,
  label,
  category,
  busy,
  onSave,
  onCancel,
}: {
  app: string;
  label: string | null;
  category: string;
  busy: boolean;
  onSave: (payload: AppRuleSave) => void;
  onCancel: () => void;
}) {
  const isSite = app.startsWith("web:");
  const value = isSite ? app.slice(4) : app;
  const [name, setName] = useState(label ?? "");
  const [cat, setCat] = useState(category);
  const inputId = `rule-name-${app}`;
  return (
    <div className="rule-edit">
      <span className="rule-edit-target">
        {isSite ? "站点规则" : "应用规则"} · <code>{value}</code>
      </span>
      <label className="sr-only" htmlFor={inputId}>
        显示名
      </label>
      <input
        id={inputId}
        className="text-input"
        placeholder="显示名（可留空）"
        value={name}
        maxLength={64}
        disabled={busy}
        onChange={(e) => setName(e.target.value)}
      />
      <label className="sr-only" htmlFor={`${inputId}-cat`}>
        行为大类
      </label>
      <select
        id={`${inputId}-cat`}
        className="text-input"
        value={cat}
        disabled={busy}
        onChange={(e) => setCat(e.target.value)}
      >
        {ONLINE_CATEGORIES.map((c) => (
          <option key={c.key} value={c.key}>
            {c.label}
          </option>
        ))}
      </select>
      <button
        className="primary small"
        disabled={busy}
        onClick={() =>
          onSave({
            match_value: value,
            scope: isSite ? "domain" : "app",
            category: cat,
            display_label: name.trim() || null,
          })
        }
      >
        保存并重算
      </button>
      <button className="secondary small" disabled={busy} onClick={onCancel}>
        取消
      </button>
    </div>
  );
}

/** 正在就地编辑的目标应用/站点。`key` 区分同源内不同容器（小时块/设备卡）。 */
interface EditTarget {
  app: string;
  label: string | null;
  category: string;
  source: "hour" | "usage" | "uncat";
  key: string;
}

interface AppPickerProps {
  editing: EditTarget | null;
  onPick: (t: EditTarget | null) => void;
  onSave: (payload: AppRuleSave) => void;
  busy: boolean;
}

/** 只在「点开的那个容器」里渲染编辑面板，避免同一应用在多处同时展开。 */
function InlineRuleEditor({
  source,
  containerKey,
  picker,
}: {
  source: EditTarget["source"];
  containerKey: string;
  picker: AppPickerProps;
}) {
  const editing = picker.editing;
  if (!editing || editing.source !== source || editing.key !== containerKey) return null;
  return (
    <AppRuleEditor
      key={editing.app}
      app={editing.app}
      label={editing.label}
      category={editing.category}
      busy={picker.busy}
      onSave={picker.onSave}
      onCancel={() => picker.onPick(null)}
    />
  );
}

function FloatingRow({ f }: { f: FloatingItem }) {
  if (f.kind === "thought") {
    const t = f.data as ThoughtItem;
    return (
      <div className="float-row thought">
        <span className="float-time">{fmtTime(f.timestamp)}</span>
        <ICONS.idea className="float-icon" size={14} aria-hidden="true" />
        <span className="float-content">{t.content}</span>
      </div>
    );
  }
  const s = f.data as StateItem;
  return (
    <div className="float-row state">
      <span className="float-time">{fmtTime(f.timestamp)}</span>
      <ICONS.state className="float-icon" size={14} aria-hidden="true" />
      <span className="float-content">
        精力 {s.energy} · 专注 {s.focus} · 烦躁 {s.irritation}
      </span>
    </div>
  );
}

function SegmentBlock({
  seg,
  onEnd,
  onDelete,
  busy,
}: {
  seg: EventSegment;
  onEnd: (id: number, endedAt: string) => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  const { label: typeLabel, icon: typeIcon } = useEventTypeResolver();
  const EventIcon = typeIcon(seg.event.type);
  const meta = { label: typeLabel(seg.event.type) };
  const isOngoing = seg.end_inferred && seg.event.ended_at === null && !seg.fuzzy;
  const multiPlatform =
    new Set(seg.device_apps.map((a) => a.platform)).size > 1;
  // 可编辑的结束时间：默认当前本地时间；点了 + 号再改成自定义
  const [customEnd, setCustomEnd] = useState<string | null>(null);
  const endTime = customEnd ?? toLocalInputValue(new Date());
  const endLabel = fmtTime(seg.end);

  return (
    <div className={"seg" + (seg.fuzzy ? " fuzzy" : "") + (isOngoing ? " ongoing" : "")}>
      <div className="seg-time">
        {fmtTime(seg.start)}
        <span className="seg-arrow">→</span>
        {endLabel}
        <span className="seg-dur">{fmtDuration(seg.duration_minutes)}</span>
      </div>
      <div className="seg-body">
        <EventIcon className="seg-emoji" size={20} weight="duotone" aria-hidden="true" />
        <span className="seg-label">{meta.label}</span>
        {seg.event.note && <span className="seg-note">{seg.event.note}</span>}
        {!seg.end_inferred && <span className="tag confirmed">已确认结束</span>}
        {seg.end_inferred && !seg.fuzzy && (
          <span className="tag inferred">结束为推断</span>
        )}
        {seg.fuzzy && (
          <>
            <span className="tag fuzzy-tag">模糊：可能仍在进行</span>
            <span className="seg-end-at">
              <label className="sr-only" htmlFor={`end-${seg.event.id}`}>
                确认结束时间
              </label>
              <input
                id={`end-${seg.event.id}`}
                type="datetime-local"
                className="text-input date"
                value={endTime}
                disabled={busy}
                onChange={(e) => setCustomEnd(e.target.value)}
              />
              <button
                className="secondary small"
                disabled={busy}
                onClick={() => onEnd(seg.event.id, new Date(endTime).toISOString())}
              >
                确认结束于所选时间
              </button>
            </span>
          </>
        )}
        <button
          className="danger small"
          disabled={busy}
          onClick={() => onDelete(seg.event.id)}
          aria-label={`删除 ${meta.label} 记录`}
          title="删除这条记录"
        >
          删除
        </button>
      </div>
      {seg.device_activities && seg.device_activities.length > 0 && (
        <div className="seg-activities">
          <ICONS.devices size={13} className="seg-device-icon" aria-hidden="true" />
          {seg.device_activities.map((a, i) => (
            <span className="seg-act" key={`${a.platform}:${a.category}:${i}`}>
              {a.platform === "android" ? "手机" : a.platform === "pc" ? "电脑" : ""}
              {categoryLabel(a.category)}
              {a.label ? ` · ${a.label}` : ""}
              <em>{fmtSeconds(a.seconds)}</em>
            </span>
          ))}
        </div>
      )}
      {seg.device_apps.length > 0 && (
        <div
          className="seg-device"
          title={seg.device_apps
            .map(
              (a) =>
                `${PLATFORM_LABELS[a.platform] ?? a.platform} ${a.label || a.app} ${fmtSeconds(a.seconds)}`,
            )
            .join("\n")}
        >
          <ICONS.devices size={13} className="seg-device-icon" aria-hidden="true" />
          {seg.device_apps.map((a) => (
            <span className="seg-device-app" key={`${a.platform}:${a.app}`}>
              {multiPlatform && (
                <em className={"platform-tag platform-" + a.platform}>
                  {PLATFORM_LABELS[a.platform] ?? a.platform}
                </em>
              )}
              {a.label || a.app} {fmtSeconds(a.seconds)}
            </span>
          ))}
        </div>
      )}
      {seg.fuzzy && seg.fuzzy_reason && (
        <div className="seg-hint">{seg.fuzzy_reason}</div>
      )}
    </div>
  );
}

function DeviceSegmentBlock({ seg }: { seg: EventSegment }) {
  const label = categoryLabel(seg.event.category) || "设备使用";
  return (
    <div className="seg device">
      <div className="seg-time">
        {fmtTime(seg.start)}
        <span className="seg-arrow">→</span>
        {fmtTime(seg.end)}
        <span className="seg-dur">{fmtDuration(seg.duration_minutes)}</span>
      </div>
      <div className="seg-body">
        <ICONS.devices className="seg-emoji" size={20} weight="duotone" aria-hidden="true" />
        <span className="seg-label">{label}</span>
        {seg.platform && (
          <span className="tag device-tag">{PLATFORM_LABELS[seg.platform] ?? seg.platform}</span>
        )}
        {seg.event.note && <span className="seg-note">{seg.event.note}</span>}
        <span className="tag device-tag">设备自动记录</span>
      </div>
    </div>
  );
}

/** 设备自动记录按小时合并后的主列表项：一行一小时，应用内联合并。 */
function DeviceHourBlock({ h, picker }: { h: DeviceHour; picker: AppPickerProps }) {
  const mainCat = h.categories.length > 0 ? categoryLabel(h.categories[0]) : "设备使用";
  const extraCats = h.categories.slice(1).map(categoryLabel).filter(Boolean);
  // 桶已跨设备合并，平台标签从应用明细里取（同一小时可能同时有电脑和手机）
  const platforms = Array.from(new Set(h.apps.map((a) => a.platform)));
  const containerKey = `hour:${h.hour}`;
  return (
    <div className="seg device hour">
      <div className="seg-time">
        {fmtTime(h.start)}
        <span className="seg-arrow">→</span>
        {fmtTime(h.end)}
        <span className="seg-dur">{fmtSeconds(h.seconds)}</span>
      </div>
      <div className="seg-body">
        <ICONS.devices className="seg-emoji" size={20} weight="duotone" aria-hidden="true" />
        <span className="seg-label">{mainCat}</span>
        {platforms.map((p) => (
          <span className="tag device-tag" key={p}>
            {PLATFORM_LABELS[p] ?? p}
          </span>
        ))}
        {extraCats.length > 0 && (
          <span className="seg-note">也含 {extraCats.join("、")}</span>
        )}
      </div>
      {h.apps.length > 0 && (
        <div
          className="seg-device"
          title={h.apps
            .map((a) => `${a.label || a.app} ${fmtSeconds(a.seconds)}`)
            .join("\n")}
        >
          <ICONS.devices size={13} className="seg-device-icon" aria-hidden="true" />
          {h.apps.map((a) => (
            <button
              className="seg-device-app pickable"
              key={`${a.platform}:${a.app}`}
              title={`点击标记这个应用（当前：${categoryLabel(a.category)}）`}
              onClick={() =>
                picker.onPick({
                  app: a.app,
                  label: a.label,
                  category: a.category,
                  source: "hour",
                  key: containerKey,
                })
              }
            >
              {a.label || a.app} {fmtSeconds(a.seconds)}
            </button>
          ))}
        </div>
      )}
      <InlineRuleEditor source="hour" containerKey={containerKey} picker={picker} />
    </div>
  );
}

function InsightCard({ item }: { item: MemoryItem }) {
  return (
    <div className="mem-item">
      <div className="mem-head">
        <span className="kind-tag mem-kind-insight">
          <ICONS.idea size={12} aria-hidden="true" /> 洞察
        </span>
        {item.has_embedding && <span className="mem-hasvec">已向量化</span>}
      </div>
      <p className="mem-content">{item.content}</p>
    </div>
  );
}

function PatternCard({ item }: { item: MemoryItem }) {
  return (
    <div className="mem-item">
      <div className="mem-head">
        <span className="kind-tag mem-kind-pattern">
          <ICONS.plant size={12} aria-hidden="true" /> 模式
        </span>
        {item.has_embedding && <span className="mem-hasvec">已向量化</span>}
      </div>
      <p className="mem-content">{item.content}</p>
    </div>
  );
}

function DeviceUsageCard({
  dev,
  picker,
}: {
  dev: UsageOverviewDevice;
  picker: AppPickerProps;
}) {
  const maxHour = Math.max(1, ...dev.hours.map((h) => h.seconds));
  const maxApp = Math.max(1, ...dev.apps.map((a) => a.seconds));
  const maxSite = Math.max(1, ...dev.websites.map((w) => w.seconds));
  const containerKey = `usage:${dev.device_id}`;
  const pick = (
    app: string,
    label: string | null,
    category: string,
  ) => {
    picker.onPick({ app, label, category, source: "usage", key: containerKey });
  };
  return (
    <div className="usage-dev">
      <div className="usage-dev-head">
        <span className={"platform-tag platform-" + dev.platform}>
          {PLATFORM_LABELS[dev.platform] ?? dev.platform}
        </span>
        <span className="usage-dev-name">{dev.name}</span>
        <span className="usage-dev-total">{fmtSeconds(dev.total_seconds)}</span>
      </div>

      <div className="usage-hours">
        <div className="usage-strip" role="img" aria-label={`${dev.name} 分小时使用`}>
          {dev.hours.map((h) => (
            <span
              className="usage-cell"
              key={h.hour}
              title={
                `${h.hour}:00 · ${fmtSeconds(h.seconds)}` +
                (h.apps.length
                  ? "\n" +
                    h.apps
                      .map((a) => `${a.label || a.app} ${fmtSeconds(a.seconds)}`)
                      .join("\n")
                  : "")
              }
              style={{ opacity: 0.1 + 0.9 * (h.seconds / maxHour) }}
            />
          ))}
        </div>
        <div className="usage-ticks">
          {[0, 6, 12, 18, 24].map((t) => (
            <span className="usage-tick" key={t}>
              {t}
            </span>
          ))}
        </div>
      </div>

      {dev.apps.length > 0 && (
        <div className="usage-rows">
          {dev.apps.map((a) => (
            <button
              className="usage-row clickable"
              key={a.app}
              title="点击标记这个应用"
              onClick={() => pick(a.app, a.label, a.category)}
            >
              <span className="usage-row-name" title={a.label || a.app}>
                {a.label || a.app}
              </span>
              <span
                className={
                  "usage-row-cat" + (a.category === "other_online" ? "" : " marked")
                }
              >
                {categoryLabel(a.category)}
              </span>
              <span className="usage-row-bar">
                <span
                  className="usage-row-fill"
                  style={{ width: `${(a.seconds / maxApp) * 100}%` }}
                />
              </span>
              <span className="usage-row-time">{fmtSeconds(a.seconds)}</span>
            </button>
          ))}
        </div>
      )}

      {dev.websites.length > 0 && (
        <div className="usage-sites">
          <div className="usage-sites-title">浏览器网站</div>
          <div className="usage-rows">
            {dev.websites.map((w) => {
              const name = w.app.replace(/^web:/, "");
              return (
                <button
                  className="usage-row usage-row-sub clickable"
                  key={w.app}
                  title="点击标记这个站点"
                  onClick={() => pick(w.app, w.label, w.category)}
                >
                  <span className="usage-row-name" title={name}>
                    {w.label || name}
                  </span>
                  <span
                    className={
                      "usage-row-cat" + (w.category === "browsing" ? "" : " marked")
                    }
                  >
                    {categoryLabel(w.category)}
                  </span>
                  <span className="usage-row-bar">
                    <span
                      className="usage-row-fill"
                      style={{ width: `${(w.seconds / maxSite) * 100}%` }}
                    />
                  </span>
                  <span className="usage-row-time">{fmtSeconds(w.seconds)}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      <InlineRuleEditor source="usage" containerKey={containerKey} picker={picker} />
    </div>
  );
}

export default function ExperienceSection() {
  const [mode, setMode] = useState<"day" | "week" | "month">("day");
  const [date, setDate] = useState(today());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [segments, setSegments] = useState<EventSegment[]>([]);
  const [floatings, setFloatings] = useState<FloatingItem[]>([]);
  const [deviceSegments, setDeviceSegments] = useState<EventSegment[]>([]);
  const [deviceHours, setDeviceHours] = useState<DeviceHour[]>([]);
  const [uncategorized, setUncategorized] = useState<UnclassifiedApp[]>([]);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dayInsights, setDayInsights] = useState<MemoryItem[]>([]);
  const [usageDevices, setUsageDevices] = useState<UsageOverviewDevice[]>([]);
  const [allInsights, setAllInsights] = useState<MemoryItem[]>([]);
  const [patterns, setPatterns] = useState<MemoryItem[]>([]);
  const [weekStart, setWeekStart] = useState<string | null>(null);
  const [weekEnd, setWeekEnd] = useState<string | null>(null);
  const [week, setWeek] = useState<string | null>(null);
  const [monthKey, setMonthKey] = useState<string | null>(null);
  const [monthPatterns, setMonthPatterns] = useState<MemoryItem[]>([]);
  const [monthInsights, setMonthInsights] = useState<MemoryItem[]>([]);

  const loadDay = useCallback(async (d: string) => {
    setError(null);
    try {
      const [tl, ins, usage, uncat] = await Promise.all([
        api.timeline(d),
        api.memoryExtractByDate(d),
        api.deviceUsageOverview(d),
        // 未识别清单是锦上添花：拿不到就不显示，不要连累整天的加载
        api.uncategorizedApps(d, d, 60).catch(() => [] as UnclassifiedApp[]),
      ]);
      setSegments(tl.segments);
      setFloatings(tl.floatings);
      setDeviceSegments(tl.device_segments ?? []);
      setDeviceHours(tl.device_hours ?? []);
      setDayInsights(ins.insights);
      setUsageDevices(usage.devices);
      setUncategorized(uncat);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadWeek = useCallback(async (d: string) => {
    setError(null);
    try {
      const [res, items] = await Promise.all([
        api.memoryExtractWeekByDate(d),
        api.memoryList(200, "insight"),
      ]);
      setPatterns(res.patterns);
      setWeek(res.week);
      setWeekStart(res.week_start);
      setWeekEnd(res.week_end);
      setAllInsights(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadMonth = useCallback(async (d: string) => {
    setError(null);
    const key = d.slice(0, 7);
    try {
      const [pats, misseds] = await Promise.all([
        api.memoryList(50, "pattern", `pattern:${key}`),
        api.memoryList(50, "insight", `insight:${key}`),
      ]);
      setMonthKey(key);
      setMonthPatterns(pats);
      setMonthInsights(misseds);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (mode === "day") loadDay(date);
    else if (mode === "week") loadWeek(date);
    else loadMonth(date);
  }, [mode, date, loadDay, loadWeek, loadMonth]);

  async function confirmEnd(eventId: number, endedAt: string) {
    setBusy(true);
    setError(null);
    try {
      await api.endEvent(eventId, endedAt);
      await loadDay(date);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** 建规则并**同步重算当天**：后台重算是异步的，这里等不起，直接调单日重算。 */
  async function saveAppRule(payload: AppRuleSave) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.createAppRule(payload);
      await api.rebuildActivity(date);
      setEditing(null);
      const name = payload.display_label || payload.match_value;
      setNotice(`已标记「${name}」→ ${categoryLabel(payload.category)} · 当天已重算`);
      await loadDay(date);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** 重算最近 30 天：区间重算走后台任务（202），所以等几秒再刷新。 */
  async function rebuildRecent() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.rebuildActivityRange(addDays(date, -29), date);
      setNotice(`已入队重算 ${res.days} 天记录，几秒后自动刷新。`);
      window.setTimeout(() => loadDay(date), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const picker: AppPickerProps = { editing, onPick: setEditing, onSave: saveAppRule, busy };

  async function confirmDelete(eventId: number) {
    if (!window.confirm("确定删除这条事件记录？此操作不可恢复。")) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEvent(eventId);
      await loadDay(date);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const insightsByDay = useMemo(() => {
    if (!weekStart || !weekEnd) return [];
    const days: { date: string; label: string; items: MemoryItem[] }[] = [];
    const total =
      (new Date(weekEnd + "T00:00:00").getTime() -
        new Date(weekStart + "T00:00:00").getTime()) /
        86400000 +
      1;
    for (let i = 0; i < total; i++) {
      const d = addDays(weekStart, i);
      days.push({
        date: d,
        label: WEEKDAYS[i] ?? d,
        items: allInsights.filter((it) => it.source_ref === `insight:${d}`),
      });
    }
    return days;
  }, [weekStart, weekEnd, allInsights]);

  /** 一条时间轴：人工段为主，未被人工段覆盖的小时用设备小时块补上。
   *
   * 「覆盖」按本地小时判定：一个段跨 21:40–22:20 就占住 21、22 两个桶，
   * 这两小时不再单独出行（段内已挂 device_activities / device_apps）。 */
  const dayRows = useMemo(() => {
    type Row =
      | { kind: "segment"; key: string; at: number; seg: EventSegment; floatings: FloatingItem[] }
      | { kind: "hour"; key: string; at: number; hour: DeviceHour };

    const segRows: Row[] = segments.map((seg, i) => {
      const segStart = new Date(seg.start).getTime();
      const segEnd = new Date(seg.end).getTime();
      const inside = floatings.filter((f) => {
        const t = new Date(f.timestamp).getTime();
        return t >= segStart && t < segEnd;
      });
      return { kind: "segment", key: `seg-${i}`, at: segStart, seg, floatings: inside };
    });

    const covered = new Set<number>();
    for (const seg of segments) {
      const s = new Date(seg.start).getTime();
      const e = new Date(seg.end).getTime();
      if (e <= s) continue;
      const h0 = new Date(s).getHours();
      const span = Math.floor((e - 1 - s) / 3600000);
      for (let k = 0; k <= span; k++) covered.add((h0 + k) % 24);
    }

    const hourRows: Row[] = deviceHours
      .filter((h) => !covered.has(h.hour))
      .map((h) => ({
        kind: "hour",
        key: `hour-${h.hour}`,
        at: new Date(h.start).getTime(),
        hour: h,
      }));

    return [...segRows, ...hourRows].sort((a, b) => a.at - b.at);
  }, [segments, floatings, deviceHours]);

  return (
    <section className="card">
      <div className="card-header">
        <h3>
          <ICONS.timer size={17} weight="duotone" aria-hidden="true" /> 个人经历
        </h3>
        <div className="ai-actions">
          <div className="mode-tabs">
            <button
              className={"mode-tab" + (mode === "day" ? " active" : "")}
              onClick={() => setMode("day")}
            >
              按日
            </button>
            <button
              className={"mode-tab" + (mode === "week" ? " active" : "")}
              onClick={() => setMode("week")}
            >
              按周
            </button>
            <button
              className={"mode-tab" + (mode === "month" ? " active" : "")}
              onClick={() => setMode("month")}
            >
              按月
            </button>
          </div>
          <label className="sr-only" htmlFor="experience-date">
            选择日期
          </label>
          <input
            id="experience-date"
            type="date"
            className="text-input date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
      </div>

      <p className="hint">
        只读视图：L0 原始记录 →（日）洞察 →（周）模式 →（月）跨周趋势；
        提炼与复盘在「助手」页进行。唯一例外：可就地确认模糊结束时间/删除原始记录。
      </p>

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          ⚠ {error}
        </div>
      )}
      {notice && (
        <p className="hint" onClick={() => setNotice(null)}>
          {notice}
        </p>
      )}

      {mode === "day" ? (
        <>
          <div className="exp-toolbar">
            <span className="muted">
              一条时间轴：人工记录为主，没被记录覆盖的时间按小时补设备使用
            </span>
          </div>
          <div className="exp-stream">
            {dayRows.length === 0 && (
              <p className="empty">这一天还没有原始记录。</p>
            )}
            {dayRows.map((row) => (
              <div className="seg-group" key={row.key}>
                {row.kind === "segment" ? (
                  <>
                    <SegmentBlock
                      seg={row.seg}
                      onEnd={confirmEnd}
                      onDelete={confirmDelete}
                      busy={busy}
                    />
                    {row.floatings.map((f, j) => (
                      <FloatingRow key={j} f={f} />
                    ))}
                  </>
                ) : (
                  <DeviceHourBlock h={row.hour} picker={picker} />
                )}
              </div>
            ))}
          </div>
          {deviceSegments.length > 0 && (
            <details className="device-raw">
              <summary className="muted">查看原始设备段（{deviceSegments.length}）</summary>
              {deviceSegments.map((seg, i) => (
                <DeviceSegmentBlock key={`dev-${i}`} seg={seg} />
              ))}
            </details>
          )}
          <h4 className="src-title">
            <ICONS.devices size={13} aria-hidden="true" /> 全天设备总览
            <button
              className="link-btn"
              disabled={busy}
              onClick={rebuildRecent}
              title="改过标记规则后，用它把历史记录按新规则重算一遍"
            >
              重算最近 30 天
            </button>
          </h4>
          {usageDevices.length === 0 && (
            <p className="empty">这一天没有设备采集数据。</p>
          )}
          {usageDevices.map((dev) => (
            <DeviceUsageCard key={dev.device_id} dev={dev} picker={picker} />
          ))}
          {uncategorized.length > 0 && (
            <details className="uncat">
              <summary className="muted">
                未识别应用（{uncategorized.length}）· 点开可逐条标记
              </summary>
              <div className="uncat-list">
                {uncategorized.map((a) => (
                  <div key={a.app}>
                    <button
                      className="uncat-row"
                      title="点击标记这个应用"
                      onClick={() =>
                        setEditing(
                          editing?.source === "uncat" && editing.app === a.app
                            ? null
                            : {
                                app: a.app,
                                label: a.label,
                                category: a.category,
                                source: "uncat",
                                key: "uncat",
                              },
                        )
                      }
                    >
                      <span className="uncat-name">{a.label || a.app}</span>
                      <span className="uncat-id">{a.app}</span>
                      <span className="uncat-time">{fmtSeconds(a.seconds)}</span>
                    </button>
                    <InlineRuleEditor source="uncat" containerKey="uncat" picker={picker} />
                  </div>
                ))}
              </div>
            </details>
          )}
          <h4 className="src-title">
            <ICONS.idea size={13} aria-hidden="true" /> 当日洞察（L1）
          </h4>
          {dayInsights.length === 0 && (
            <p className="empty">尚未提炼当日洞察（在「助手」页生成日复盘时自动提炼）。</p>
          )}
          {dayInsights.map((it) => (
            <InsightCard key={it.id} item={it} />
          ))}
        </>
      ) : mode === "week" ? (
        <>
          <div className="exp-toolbar">
            <span className="muted">
              {week
                ? `${week}（${weekStart} ~ ${weekEnd}）`
                : "按 ISO 周对齐（周一 ~ 周日）"}
            </span>
          </div>
          <h4 className="src-title">
            <ICONS.plant size={13} aria-hidden="true" /> 长期模式（L2）
          </h4>
          {patterns.length === 0 && (
            <p className="empty">本周尚未提炼模式（在「助手」页生成周复盘时提炼）。</p>
          )}
          {patterns.map((it) => (
            <PatternCard key={it.id} item={it} />
          ))}
          <h4 className="src-title">
            <ICONS.idea size={13} aria-hidden="true" /> 一周洞察（L1）
          </h4>
          {insightsByDay.every((d) => d.items.length === 0) && (
            <p className="empty">本周还没有已提炼的洞察。</p>
          )}
          {insightsByDay
            .filter((d) => d.items.length > 0)
            .map((d) => (
              <div key={d.date} className="exp-day-group">
                <div className="exp-day-label">
                  {d.label} · {d.date}
                </div>
                {d.items.map((it) => (
                  <InsightCard key={it.id} item={it} />
                ))}
              </div>
            ))}
        </>
      ) : (
        <>
          <div className="exp-toolbar">
            <span className="muted">
              {monthKey ? `${monthKey} 月` : "按自然月对齐"}
            </span>
          </div>
          <h4 className="src-title">
            <ICONS.plant size={13} aria-hidden="true" /> 月度模式（跨周）
          </h4>
          {monthPatterns.length === 0 && (
            <p className="empty">本月尚未提炼跨周模式（在「助手」页生成月复盘时提炼）。</p>
          )}
          {monthPatterns.map((it) => (
            <PatternCard key={it.id} item={it} />
          ))}
          <h4 className="src-title">
            <ICONS.idea size={13} aria-hidden="true" /> 月度补充洞察
          </h4>
          {monthInsights.length === 0 && <p className="empty">本月还没有补充洞察。</p>}
          {monthInsights.map((it) => (
            <InsightCard key={it.id} item={it} />
          ))}
        </>
      )}
    </section>
  );
}
