import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArchiveIcon,
  ArrowUUpLeftIcon,
  PencilSimpleIcon,
  PlusIcon,
  SparkleIcon,
  TrashIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import { api } from "../../lib/api";
import type {
  ProfileCategory,
  ProfileFact,
  PromotionCandidate,
} from "../../lib/api";
import { PROFILE_CATEGORIES, profileCategoryLabel } from "../../lib/constants";
import { useAiBlocked } from "../../lib/aiGate";
import AiDemoNotice from "../AiDemoNotice";

const emptyForm = { category: "habit" as ProfileCategory, content: "" };

interface PromotionEdit extends PromotionCandidate {
  checked: boolean;
}

export default function ProfileSection() {
  const [facts, setFacts] = useState<ProfileFact[]>([]);
  // 演示账号：晋升扫描要烧 LLM，手工添加背景不烧 —— 只禁扫描。
  const aiBlocked = useAiBlocked();
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);

  // 画像晋升：扫描跨周/跨天复现 → 勾选确认写入
  const [scanning, setScanning] = useState(false);
  const [promotions, setPromotions] = useState<PromotionEdit[]>([]);
  const [scanned, setScanned] = useState(false);
  const [scanMeta, setScanMeta] = useState<{
    patterns: number;
    insights: number;
    mode: string;
  } | null>(null);
  const [committing, setCommitting] = useState(false);
  const [promotionMsg, setPromotionMsg] = useState<string | null>(null);

  const load = useCallback(async (includeArchived: boolean) => {
    setError(null);
    try {
      setFacts(await api.profileFacts(includeArchived));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load(showArchived);
  }, [load, showArchived]);

  const grouped = useMemo(
    () =>
      PROFILE_CATEGORIES.map((c) => ({
        ...c,
        items: facts.filter((f) => f.category === c.key),
      })).filter((g) => g.items.length > 0),
    [facts],
  );

  async function scanPromotions() {
    setScanning(true);
    setError(null);
    setPromotionMsg(null);
    try {
      const r = await api.scanPromotions();
      setPromotions(r.candidates.map((c) => ({ ...c, checked: true })));
      setScanMeta({
        patterns: r.scanned_patterns,
        insights: r.scanned_insights,
        mode: r.mode,
      });
      setScanned(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }

  function patchPromotion(index: number, patch: Partial<PromotionEdit>) {
    setPromotions((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }

  async function commitPromotions() {
    const selected = promotions.filter((p) => p.checked && p.content.trim());
    if (selected.length === 0) return;
    setCommitting(true);
    setError(null);
    setPromotionMsg(null);
    try {
      const payload: PromotionCandidate[] = selected.map((p) => ({
        content: p.content.trim(),
        category: p.category,
        confidence: p.confidence,
        source_ref: p.source_ref,
        source_kind: p.source_kind,
        spans: p.spans,
        avg_similarity: p.avg_similarity,
        members: p.members,
      }));
      const r = await api.commitPromotions(payload);
      const parts: string[] = [];
      if (r.fact_ids.length) parts.push(`已写入 ${r.fact_ids.length} 条`);
      if (r.skipped) parts.push(`${r.skipped} 条重复/超限被跳过`);
      setPromotionMsg(parts.length ? parts.join("，") + "。" : "没有可写入的候选。");
      setPromotions((rows) => rows.filter((p) => !p.checked || !p.content.trim()));
      load(showArchived);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommitting(false);
    }
  }

  async function submit() {
    if (!form.content.trim()) return;
    setError(null);
    try {
      if (editingId !== null) {
        await api.updateProfileFact(editingId, {
          category: form.category,
          content: form.content.trim(),
        });
      } else {
        await api.createProfileFact({
          category: form.category,
          content: form.content.trim(),
        });
      }
      setForm(emptyForm);
      setEditingId(null);
      setShowAdd(false);
      load(showArchived);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function startEdit(fact: ProfileFact) {
    setForm({ category: fact.category, content: fact.content });
    setEditingId(fact.id);
    setShowAdd(true);
  }

  async function toggleArchive(fact: ProfileFact) {
    setError(null);
    try {
      await api.updateProfileFact(fact.id, {
        status: fact.status === "active" ? "archived" : "active",
      });
      load(showArchived);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function remove(fact: ProfileFact) {
    if (!window.confirm(`确定删除背景「${fact.content}」？此操作不可恢复。`)) return;
    setError(null);
    try {
      await api.deleteProfileFact(fact.id);
      load(showArchived);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <div className="card-header">
        <p className="hint">
          跨话题稳定、3 个月后仍成立的一句话事实，会作为「既定前提」注入 AI
          问答；具体事件细节请留在记录里。AI 推断须经你确认才会进入这里。
        </p>
        <div className="ai-actions">
          <button
            className="secondary small"
            disabled={scanning || aiBlocked}
            onClick={scanPromotions}
          >
            <SparkleIcon size={13} weight="duotone" aria-hidden="true" />
            {scanning ? "扫描中…" : "AI 晋升候选"}
          </button>
          <button className="primary small" onClick={() => setShowAdd((s) => !s)}>
            {showAdd ? (
              "取消"
            ) : (
              <>
                <PlusIcon size={13} weight="bold" aria-hidden="true" /> 添加背景
              </>
            )}
          </button>
        </div>
      </div>

      {aiBlocked && <AiDemoNotice />}

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          <WarningIcon size={14} aria-hidden="true" /> {error}（点击关闭）
        </div>
      )}

      {scanned && (
        <section className="card expand-enter">
          <h3>晋升候选（跨周/跨天复现）</h3>
          <p className="hint">
            扫描了 {scanMeta?.patterns ?? 0} 条模式、{scanMeta?.insights ?? 0} 条洞察
            {scanMeta?.mode === "raw" && "（AI 精炼未生效，为原始表述）"}
            ；确认后写入用户背景，AI 推断会标注来源。
          </p>

          {promotions.length === 0 && (
            <p className="empty">没有新的可晋升候选（已晋升或复现不足两次）。</p>
          )}

          {promotions.map((p, i) => (
            <div className="candidate" key={i}>
              <label className="candidate-check">
                <input
                  type="checkbox"
                  checked={p.checked}
                  onChange={(e) => patchPromotion(i, { checked: e.target.checked })}
                />
              </label>
              <div className="candidate-body">
                <div className="candidate-meta">
                  <select
                    className="text-input narrow"
                    value={p.category}
                    aria-label={`候选 ${i + 1} 分类`}
                    onChange={(e) =>
                      patchPromotion(i, {
                        category: e.target.value as ProfileCategory,
                      })
                    }
                  >
                    {PROFILE_CATEGORIES.map((c) => (
                      <option key={c.key} value={c.key}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <span className="candidate-conf">
                    {p.source_kind === "pattern" ? "模式" : "洞察"} · 跨{" "}
                    {p.spans.length} 个期间
                  </span>
                  <span className="candidate-conf">
                    置信度 {Math.round(p.confidence * 100)}%
                  </span>
                </div>
                <input
                  className="text-input"
                  value={p.content}
                  aria-label={`候选 ${i + 1} 内容`}
                  onChange={(e) => patchPromotion(i, { content: e.target.value })}
                />
                <p className="candidate-note">
                  {p.spans.join(" / ")}
                  {p.members.length > 0 &&
                    ` · 依据 ${p.members.length} 条：` +
                      p.members
                        .map((m) => m.ref?.split(":", 2)[1] ?? "")
                        .filter(Boolean)
                        .join("、")}
                </p>
              </div>
            </div>
          ))}

          {promotions.length > 0 && (
            <div className="ai-actions">
              <button
                className="primary small"
                disabled={
                  committing || promotions.filter((p) => p.checked).length === 0
                }
                onClick={commitPromotions}
              >
                {committing
                  ? "写入中…"
                  : `写入所选（${promotions.filter((p) => p.checked).length}）`}
              </button>
            </div>
          )}
          {promotionMsg && <p className="hint sync-msg">{promotionMsg}</p>}
        </section>
      )}

      {showAdd && (
        <section className="card">
          <h3>{editingId !== null ? "编辑背景" : "添加一条用户背景"}</h3>
          <select
            className="text-input date"
            value={form.category}
            onChange={(e) =>
              setForm((f) => ({ ...f, category: e.target.value as ProfileCategory }))
            }
            aria-label="背景分类"
          >
            {PROFILE_CATEGORIES.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
          <textarea
            className="text-input tall"
            placeholder="一句话，无日期：如「压力大时倾向熬夜刷手机」"
            value={form.content}
            onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
          />
          <div className="ai-actions">
            <button
              className="primary"
              disabled={!form.content.trim()}
              onClick={submit}
            >
              保存背景
            </button>
          </div>
        </section>
      )}

      <label className="archive-toggle muted">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(e) => setShowArchived(e.target.checked)}
        />
        显示已归档
      </label>

      {grouped.length === 0 && !showAdd && (
        <p className="empty card">
          还没有背景。写下那些「换个话题也依然成立」的自我描述，比如长期习惯、偏好、限制。
        </p>
      )}

      {grouped.map((g) => (
        <section className="card" key={g.key}>
          <h4 className="src-title">{g.label}</h4>
          {g.items.map((fact) => (
            <div
              className={"mem-item" + (fact.status === "archived" ? " archived" : "")}
              key={fact.id}
            >
              <div className="mem-head">
                <span className="kind-tag mem-kind-profile">
                  {profileCategoryLabel(fact.category)}
                </span>
                {fact.source === "ai" && (
                  <span className="mem-kind-ai">AI 候选（已确认）</span>
                )}
                {fact.status === "archived" && (
                  <span className="mem-hasvec">已归档</span>
                )}
              </div>
              <p className="mem-content">{fact.content}</p>
              <div className="finding-ops">
                <button
                  className="link-btn"
                  onClick={() => startEdit(fact)}
                  aria-label={`编辑背景：${fact.content}`}
                  title="编辑"
                >
                  <PencilSimpleIcon size={14} aria-hidden="true" />
                </button>
                <button
                  className="link-btn"
                  onClick={() => toggleArchive(fact)}
                  aria-label={
                    fact.status === "active"
                      ? `归档背景：${fact.content}`
                      : `恢复背景：${fact.content}`
                  }
                  title={fact.status === "active" ? "归档（不再注入）" : "恢复"}
                >
                  {fact.status === "active" ? (
                    <ArchiveIcon size={14} aria-hidden="true" />
                  ) : (
                    <ArrowUUpLeftIcon size={14} aria-hidden="true" />
                  )}
                </button>
                <button
                  className="link-btn danger-link"
                  onClick={() => remove(fact)}
                  aria-label={`删除背景：${fact.content}`}
                  title="删除"
                >
                  <TrashIcon size={14} aria-hidden="true" />
                </button>
              </div>
            </div>
          ))}
        </section>
      ))}
    </>
  );
}
