import { useCallback, useEffect, useState } from "react";
import {
  ArrowClockwiseIcon,
  CalendarBlankIcon,
  CalendarDotsIcon,
  CalendarStarIcon,
  TrashIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { api, today } from "../../lib/api";
import type { PeriodReviewRead, ReviewIndexItem, ReviewRead } from "../../lib/api";
import { renderMarkdown } from "../../lib/markdown";
import { useAiBlocked } from "../../lib/aiGate";
import AiDemoNotice from "../AiDemoNotice";

type Kind = "day" | "week" | "month";

const KINDS: { key: Kind; label: string; icon: Icon }[] = [
  { key: "day", label: "日", icon: CalendarDotsIcon },
  { key: "week", label: "周", icon: CalendarBlankIcon },
  { key: "month", label: "月", icon: CalendarStarIcon },
];

const EMPTY: Record<Kind, string> = {
  day: "还没有日复盘。点上面的按钮就能补一篇。",
  week: "还没有周复盘。",
  month: "还没有月复盘。月复盘需要至少两篇已完成的周复盘做素材。",
};

const HINT: Record<Kind, string> = {
  day: "每天自动补上昨天的；也可以随时手动生成。",
  week: "每周一自动补上一周的；也可以随时手动生成。",
  month: "每月 1 号自动补上一个月的；也可以随时手动生成。",
};

function fmtTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

/** 助手页「复盘」标签：翻历史复盘 + 手动生成，正文与对话共用同一套 markdown 渲染。 */
export default function ReviewSection() {
  const aiBlocked = useAiBlocked();
  const [kind, setKind] = useState<Kind>("day");
  const [items, setItems] = useState<ReviewIndexItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewRead | PeriodReviewRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedItem = items.find((i) => i.key === selected) ?? null;

  const loadIndex = useCallback(async () => {
    try {
      setItems(await api.reviewsIndex());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    loadIndex();
  }, [loadIndex]);

  // 选中的那篇不在当前档里（切换档、或刚被删掉）→ 自动落到该档最新的一篇
  useEffect(() => {
    const inKind = items.filter((i) => i.kind === kind);
    if (inKind.some((i) => i.key === selected)) return;
    setSelected(inKind[0]?.key ?? null);
  }, [kind, items, selected]);

  useEffect(() => {
    if (!selectedItem) {
      setDetail(null);
      return;
    }
    // 周/月的详情端点收的是日期而不是 period_key
    const date =
      selectedItem.kind === "day" ? selectedItem.key : selectedItem.period_start;
    if (!date) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    const req =
      selectedItem.kind === "day"
        ? api.getReview(date)
        : api.getPeriodReview(selectedItem.kind, date);
    req
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e) => {
        if (!cancelled) {
          setDetail(null);
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedItem]);

  // 生成目标 = 当前选中的那一期；没有就落回当前这一期
  const genDate =
    selectedItem?.kind === "day"
      ? selectedItem.key
      : (selectedItem?.period_start ?? today());

  async function generate() {
    if (aiBlocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      // 已经有正文时按「重新生成」办：force 会真的再调一次 LLM
      const force = detail !== null;
      let key: string;
      if (kind === "day") {
        key = (await api.generateReview(genDate, force)).date;
      } else {
        key = (await api.generatePeriodReview(kind, genDate, force)).period_key;
      }
      await loadIndex();
      setSelected(key);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(item: ReviewIndexItem) {
    if (busy) return;
    if (
      !window.confirm(
        `确定删除 ${item.label} 的复盘？与它一起进记忆索引的那几条洞察也会一并清掉，此操作不可恢复。`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.deleteReview(item.key);
      setSelected(null);
      setDetail(null);
      await loadIndex();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const kindItems = items.filter((i) => i.kind === kind);

  return (
    <>
      <div className="section-tabs">
        {KINDS.map((k) => {
          const KindIcon = k.icon;
          return (
            <button
              key={k.key}
              className={"section-tab" + (kind === k.key ? " active" : "")}
              onClick={() => setKind(k.key)}
            >
              <KindIcon size={15} aria-hidden="true" /> {k.label}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          <WarningIcon size={14} aria-hidden="true" /> {error}（点击关闭）
        </div>
      )}

      <div className="agent-wrap">
        <aside className="agent-rail review-rail">
          <div className="agent-rail-head">
            <span className="agent-rail-label">
              {KINDS.find((k) => k.key === kind)?.label}复盘 · {kindItems.length} 篇
            </span>
          </div>
          {kindItems.length === 0 && <p className="hint">{EMPTY[kind]}</p>}
          {kindItems.map((item) => (
            <button
              key={item.key}
              type="button"
              className={"review-item" + (item.key === selected ? " active" : "")}
              onClick={() => setSelected(item.key)}
              title={item.label}
            >
              <span className="review-item-label">
                {item.label}
                {item.stale && <span className="review-stale">素材已更新</span>}
              </span>
              {item.summary && (
                <span className="review-item-summary">{item.summary}</span>
              )}
            </button>
          ))}
        </aside>

        <section className="card agent-main">
          <div className="review-actions">
            <button className="primary small" disabled={aiBlocked || busy} onClick={generate}>
              <ArrowClockwiseIcon size={13} aria-hidden="true" />{" "}
              {busy ? "生成中…" : detail ? "重新生成" : "生成"}
            </button>
            {selectedItem?.kind === "day" && (
              <button
                className="secondary small"
                disabled={busy || aiBlocked}
                onClick={() => remove(selectedItem)}
              >
                <TrashIcon size={13} aria-hidden="true" /> 删除这篇
              </button>
            )}
            <span className="hint">{HINT[kind]}</span>
          </div>

          {aiBlocked && <AiDemoNotice />}

          {!selectedItem ? (
            <p className="empty">{EMPTY[kind]}</p>
          ) : !detail ? (
            <p className="empty">读取中…</p>
          ) : (
            <>
              <div
                className="markdown-body"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(detail.review_text) }}
              />
              <p className="review-meta">
                {detail.model ?? "unknown"} · 更新于 {fmtTime(detail.updated_at)}
              </p>
              {detail.status !== "ok" && (
                <p className="hint">这次生成没成功：{detail.error ?? "未知错误"}</p>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
