import { useCallback, useEffect, useState } from "react";
import {
  FlaskIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
  WarningIcon,
} from "@phosphor-icons/react";
import { api } from "../../lib/api";
import type {
  ExperimentDraft,
  Finding,
  FindingKind,
  MetricDef,
  Problem,
  ProblemStatus,
} from "../../lib/api";
import { METRIC_SOURCES } from "../../lib/constants";
import { useAiBlocked } from "../../lib/aiGate";
import AiDemoNotice from "../AiDemoNotice";

const STATUS_LABELS: Record<ProblemStatus, string> = {
  OPEN: "待解决",
  DORMANT: "搁置",
  RESOLVED: "已解决",
};

const KIND_LABELS: Record<FindingKind, string> = {
  OBSERVATION: "观察（事实）",
  HYPOTHESIS: "假设（推测）",
  CONCLUSION: "结论",
};

const DIRECTION_LABELS: Record<string, string> = {
  up_good: "越高越好",
  down_good: "越低越好",
  neutral: "中性",
};

const emptyProblemForm = { title: "", category: "", note: "" };
const emptyFindingForm = {
  title: "",
  kind: "HYPOTHESIS" as FindingKind,
  observation: "",
  evidence: "",
  interpretation: "",
  next_step: "",
};

export default function KnowledgeSection() {
  const [problems, setProblems] = useState<Problem[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  // 演示账号：AI 设计实验要烧 LLM（创建草稿本身不烧，手工填也一样）。
  const aiBlocked = useAiBlocked();
  const [activeId, setActiveId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState(emptyProblemForm);
  const [editingProblemId, setEditingProblemId] = useState<number | null>(null);
  const [showFinding, setShowFinding] = useState(false);
  const [fForm, setFForm] = useState(emptyFindingForm);
  const [editingFindingId, setEditingFindingId] = useState<number | null>(null);

  // AI 实验设计（草稿 → 用户编辑确认 → 创建 DRAFT 实验）
  const [draft, setDraft] = useState<ExperimentDraft | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [creatingExp, setCreatingExp] = useState(false);
  const [expMsg, setExpMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setProblems(await api.problems());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const loadFindings = useCallback(async (problemId: number) => {
    try {
      setFindings(await api.findings(problemId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  function selectProblem(p: Problem) {
    setActiveId(p.id);
    setDraft(null);
    setExpMsg(null);
    loadFindings(p.id);
  }

  async function designExperiment() {
    if (activeId === null) return;
    setDraftBusy(true);
    setError(null);
    setExpMsg(null);
    try {
      setDraft(await api.designExperiment(activeId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDraftBusy(false);
    }
  }

  function patchDraft(patch: Partial<ExperimentDraft>) {
    setDraft((d) => (d ? { ...d, ...patch } : d));
  }

  function patchMetric(index: number, patch: Partial<MetricDef>) {
    setDraft((d) =>
      d
        ? {
            ...d,
            metrics: d.metrics.map((m, i) => (i === index ? { ...m, ...patch } : m)),
          }
        : d,
    );
  }

  function removeMetric(index: number) {
    setDraft((d) =>
      d ? { ...d, metrics: d.metrics.filter((_, i) => i !== index) } : d,
    );
  }

  function addMetric() {
    setDraft((d) =>
      d
        ? {
            ...d,
            metrics: [
              ...d.metrics,
              {
                key: `metric_${d.metrics.length + 1}`,
                name: "",
                unit: null,
                direction: "neutral",
                source: "manual",
              },
            ],
          }
        : d,
    );
  }

  async function createFromDraft() {
    if (!draft) return;
    const metrics = draft.metrics.filter((m) => m.name.trim());
    if (!draft.name.trim() || metrics.length === 0) {
      setError("实验名与至少一个指标名不能为空。");
      return;
    }
    setCreatingExp(true);
    setError(null);
    try {
      const exp = await api.createExperiment({
        name: draft.name.trim(),
        question: draft.question.trim() || undefined,
        hypothesis: draft.hypothesis.trim() || undefined,
        variable: draft.variable.trim() || undefined,
        indicator: draft.indicator.trim() || undefined,
        metrics,
        expected_days: draft.expected_days,
        baseline_note: draft.baseline_note?.trim() || undefined,
      });
      setDraft(null);
      setExpMsg(
        `已创建实验草稿「${exp.name}」（#${exp.id}）；到「实验」页检查后启动。`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreatingExp(false);
    }
  }

  async function addProblem() {
    if (!form.title.trim()) return;
    setError(null);
    try {
      const payload = {
        title: form.title.trim(),
        category: form.category.trim() || undefined,
        note: form.note.trim() || undefined,
      };
      if (editingProblemId !== null) {
        await api.updateProblem(editingProblemId, payload);
      } else {
        await api.createProblem(payload);
      }
      setForm(emptyProblemForm);
      setEditingProblemId(null);
      setShowAdd(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function startEditProblem(p: Problem) {
    setForm({
      title: p.title,
      category: p.category ?? "",
      note: p.note ?? "",
    });
    setEditingProblemId(p.id);
    setShowAdd(true);
  }

  async function removeProblem(p: Problem) {
    if (!window.confirm(`确定删除问题「${p.title}」？此操作不可恢复。`)) return;
    setError(null);
    try {
      await api.deleteProblem(p.id);
      if (activeId === p.id) setActiveId(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function setStatus(p: Problem, status: ProblemStatus) {
    setError(null);
    try {
      await api.updateProblem(p.id, { status });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function addFinding() {
    if (!fForm.title.trim() || !fForm.observation.trim() || activeId === null) return;
    setError(null);
    const payload = {
      problem_id: activeId,
      kind: fForm.kind,
      title: fForm.title.trim(),
      observation: fForm.observation.trim(),
      evidence: fForm.evidence.trim() || undefined,
      interpretation: fForm.interpretation.trim() || undefined,
      next_step: fForm.next_step.trim() || undefined,
    };
    try {
      if (editingFindingId !== null) {
        await api.updateFinding(editingFindingId, payload);
        setEditingFindingId(null);
      } else {
        await api.createFinding(payload);
      }
      setFForm(emptyFindingForm);
      setShowFinding(false);
      loadFindings(activeId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function startEditFinding(f: Finding) {
    setFForm({
      title: f.title ?? "",
      kind: f.kind,
      observation: f.observation ?? "",
      evidence: f.evidence ?? "",
      interpretation: f.interpretation ?? "",
      next_step: f.next_step ?? "",
    });
    setEditingFindingId(f.id);
    setShowFinding(true);
  }

  async function removeFinding(f: Finding) {
    if (!window.confirm(`确定删除发现「${f.title}」？此操作不可恢复。`)) return;
    setError(null);
    try {
      await api.deleteFinding(f.id);
      if (activeId !== null) loadFindings(activeId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <div className="card-header">
        <p className="hint">
          问题 = 长期在追问什么；发现 = 从经历中沉淀的事实/假设/结论（AI 推测均标注）。
        </p>
        <button className="primary small" onClick={() => setShowAdd((s) => !s)}>
          {showAdd ? (
            "取消"
          ) : (
            <>
              <PlusIcon size={13} weight="bold" aria-hidden="true" /> 新问题
            </>
          )}
        </button>
      </div>

      {aiBlocked && <AiDemoNotice />}

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          <WarningIcon size={14} aria-hidden="true" /> {error}（点击关闭）
        </div>
      )}

      {showAdd && (
        <section className="card">
          <h3>记录一个新问题</h3>
          <input
            className="text-input"
            placeholder="问题标题 *：如「为什么学习前会逃避？」"
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
          />
          <input
            className="text-input"
            placeholder="分类（可选）：如 精力 / 学习 / 情绪"
            value={form.category}
            onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
          />
          <textarea
            className="text-input tall"
            placeholder="补充说明（可选）"
            value={form.note}
            onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
          />
          <button className="primary" disabled={!form.title.trim()} onClick={addProblem}>
            保存问题
          </button>
        </section>
      )}

      {problems.length === 0 && !showAdd && (
        <p className="empty card">
          还没有问题。把长期困扰你的行为模式记下来，然后用实验逐个验证。
        </p>
      )}

      <div className="knowledge-split">
        <section className="card problem-list">
          {problems.map((p) => (
            <div
              key={p.id}
              className={"problem-item" + (activeId === p.id ? " active" : "")}
            >
              <button
                className="problem-open"
                onClick={() => selectProblem(p)}
                aria-pressed={activeId === p.id}
              >
                <span className="problem-title">
                  <span className={`status status-${p.status.toLowerCase()}`}>
                    {STATUS_LABELS[p.status]}
                  </span>
                  {p.title}
                </span>
                {p.category && <span className="problem-cat">#{p.category}</span>}
              </button>
              <span className="problem-ops">
                <button
                  className="link-btn"
                  onClick={() => startEditProblem(p)}
                  aria-label={`编辑问题：${p.title}`}
                  title="编辑"
                >
                  <PencilSimpleIcon size={14} aria-hidden="true" />
                </button>
                <button
                  className="link-btn danger-link"
                  onClick={() => removeProblem(p)}
                  aria-label={`删除问题：${p.title}`}
                  title="删除"
                >
                  <TrashIcon size={14} aria-hidden="true" />
                </button>
              </span>
            </div>
          ))}
        </section>

        <section className="card problem-detail">
          {activeId === null ? (
            <p className="empty">选择一个问题查看分析记录。</p>
          ) : (
            <>
              <div className="card-header">
                <h3>{problems.find((p) => p.id === activeId)?.title ?? "问题"}</h3>
                <div className="ai-actions">
                  {problems.find((p) => p.id === activeId)?.status !== "RESOLVED" && (
                    <button
                      className="secondary small"
                      onClick={() =>
                        setStatus(problems.find((p) => p.id === activeId)!, "RESOLVED")
                      }
                    >
                      标记为已解决
                    </button>
                  )}
                  <button
                    className="secondary small"
                    disabled={draftBusy || aiBlocked}
                    onClick={designExperiment}
                  >
                    <FlaskIcon size={13} weight="duotone" aria-hidden="true" />
                    {draftBusy ? "AI 设计中…" : "设计实验"}
                  </button>
                  <button
                    className="secondary small"
                    onClick={() => setShowFinding((s) => !s)}
                  >
                    {showFinding ? (
                      "取消"
                    ) : editingFindingId !== null ? (
                      "编辑发现中…"
                    ) : (
                      <>
                        <PlusIcon size={13} weight="bold" aria-hidden="true" /> 添加发现
                      </>
                    )}
                  </button>
                </div>
              </div>

              {expMsg && <p className="hint sync-msg">{expMsg}</p>}

              {draft && (
                <div className="draft-form expand-enter">
                  <p className="hint">
                    AI 实验草稿（可编辑）；确认后创建为「草稿」状态的实验，不会自动开始。
                    {draft.rationale && ` 思路：${draft.rationale}`}
                    {draft.evidence.length > 0 &&
                      ` · 依据 ${draft.evidence.length} 条（发现/记忆）`}
                  </p>
                  <label className="sr-only" htmlFor="draft-name">
                    实验名
                  </label>
                  <input
                    id="draft-name"
                    className="text-input"
                    placeholder="实验名 *"
                    value={draft.name}
                    onChange={(e) => patchDraft({ name: e.target.value })}
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="要回答的问题"
                    value={draft.question}
                    onChange={(e) => patchDraft({ question: e.target.value })}
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="假设（可证伪）*"
                    value={draft.hypothesis}
                    onChange={(e) => patchDraft({ hypothesis: e.target.value })}
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="改变的变量：具体到可执行动作"
                    value={draft.variable}
                    onChange={(e) => patchDraft({ variable: e.target.value })}
                  />
                  <input
                    className="text-input"
                    placeholder="观察指标说明"
                    value={draft.indicator}
                    onChange={(e) => patchDraft({ indicator: e.target.value })}
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="基线说明（对照用）"
                    value={draft.baseline_note ?? ""}
                    onChange={(e) => patchDraft({ baseline_note: e.target.value })}
                  />

                  <div className="draft-metrics">
                    <div className="draft-metrics-head">
                      <span className="muted">指标（自动数据源按天聚合）</span>
                      <button className="link-btn" onClick={addMetric}>
                        <PlusIcon size={13} aria-hidden="true" /> 添加
                      </button>
                    </div>
                    {draft.metrics.map((m, i) => (
                      <div className="draft-metric" key={i}>
                        <input
                          className="text-input"
                          placeholder="指标名 *"
                          aria-label={`指标 ${i + 1} 名称`}
                          value={m.name}
                          onChange={(e) => patchMetric(i, { name: e.target.value })}
                        />
                        <select
                          className="text-input narrow"
                          aria-label={`指标 ${i + 1} 方向`}
                          value={m.direction}
                          onChange={(e) =>
                            patchMetric(i, {
                              direction: e.target.value as MetricDef["direction"],
                            })
                          }
                        >
                          {Object.entries(DIRECTION_LABELS).map(([k, label]) => (
                            <option key={k} value={k}>
                              {label}
                            </option>
                          ))}
                        </select>
                        <select
                          className="text-input narrow source"
                          aria-label={`指标 ${i + 1} 数据源`}
                          value={m.source ?? "manual"}
                          onChange={(e) => patchMetric(i, { source: e.target.value })}
                        >
                          {METRIC_SOURCES.map((s) => (
                            <option key={s.value} value={s.value}>
                              {s.label}
                            </option>
                          ))}
                        </select>
                        <button
                          className="link-btn danger-link"
                          onClick={() => removeMetric(i)}
                          aria-label={`删除指标 ${m.name || i + 1}`}
                        >
                          <TrashIcon size={14} aria-hidden="true" />
                        </button>
                      </div>
                    ))}
                  </div>

                  <div className="draft-footer">
                    <label className="draft-days">
                      预计周期
                      <input
                        className="text-input narrow"
                        type="number"
                        min={7}
                        max={30}
                        value={draft.expected_days}
                        onChange={(e) =>
                          patchDraft({
                            expected_days: Math.max(
                              7,
                              Math.min(30, Number(e.target.value) || 14),
                            ),
                          })
                        }
                      />
                      天
                    </label>
                    <div className="ai-actions">
                      <button
                        className="primary small"
                        disabled={creatingExp || !draft.name.trim()}
                        onClick={createFromDraft}
                      >
                        {creatingExp ? "创建中…" : "创建实验（草稿）"}
                      </button>
                      <button
                        className="secondary small"
                        onClick={() => setDraft(null)}
                      >
                        放弃草稿
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {showFinding && (
                <div className="finding-form expand-enter">
                  <select
                    className="text-input date"
                    value={fForm.kind}
                    onChange={(e) =>
                      setFForm((f) => ({ ...f, kind: e.target.value as FindingKind }))
                    }
                  >
                    {(["OBSERVATION", "HYPOTHESIS", "CONCLUSION"] as const).map((k) => (
                      <option key={k} value={k}>
                        {KIND_LABELS[k]}
                      </option>
                    ))}
                  </select>
                  <input
                    className="text-input"
                    placeholder="发现标题 *"
                    value={fForm.title}
                    onChange={(e) => setFForm((f) => ({ ...f, title: e.target.value }))}
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="观察（事实）：发生了什么、数据如何 *"
                    value={fForm.observation}
                    onChange={(e) =>
                      setFForm((f) => ({ ...f, observation: e.target.value }))
                    }
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="证据来源（可选）：哪天的记录、哪个实验"
                    value={fForm.evidence}
                    onChange={(e) => setFForm((f) => ({ ...f, evidence: e.target.value }))}
                  />
                  <textarea
                    className="text-input tall"
                    placeholder="可能解释（推测）：为什么？"
                    value={fForm.interpretation}
                    onChange={(e) =>
                      setFForm((f) => ({ ...f, interpretation: e.target.value }))
                    }
                  />
                  <input
                    className="text-input"
                    placeholder="下一步（可选）"
                    value={fForm.next_step}
                    onChange={(e) =>
                      setFForm((f) => ({ ...f, next_step: e.target.value }))
                    }
                  />
                  <button
                    className="primary"
                    disabled={!fForm.title.trim() || !fForm.observation.trim()}
                    onClick={addFinding}
                  >
                    保存发现
                  </button>
                </div>
              )}

              <div className="finding-list">
                {findings.length === 0 && (
                  <p className="empty">这个问题还没有分析记录。</p>
                )}
                {findings.map((f) => (
                  <div className={`finding finding-${f.kind.toLowerCase()}`} key={f.id}>
                    <div className="finding-head">
                      <span className="kind-tag">{KIND_LABELS[f.kind]}</span>
                      <span className="finding-title">{f.title}</span>
                    </div>
                    <p>
                      <b>观察：</b>
                      {f.observation}
                    </p>
                    {f.evidence && (
                      <p className="finding-ev">
                        <b>证据：</b>
                        {f.evidence}
                      </p>
                    )}
                    {f.interpretation && (
                      <p className="finding-interp">
                        <b>可能解释（推测）：</b>
                        {f.interpretation}
                      </p>
                    )}
                    {f.next_step && (
                      <p className="finding-next">
                        <b>下一步：</b>
                        {f.next_step}
                      </p>
                    )}
                    <div className="finding-foot">
                      <span className="confidence">
                        可信度 {Math.round(f.confidence * 100)}%
                      </span>
                      <div className="finding-ops">
                        <button
                          className="link-btn"
                          onClick={() => startEditFinding(f)}
                          aria-label={`编辑发现：${f.title}`}
                        >
                          <PencilSimpleIcon size={14} aria-hidden="true" />
                        </button>
                        <button
                          className="link-btn danger-link"
                          onClick={() => removeFinding(f)}
                          aria-label={`删除发现：${f.title}`}
                        >
                          <TrashIcon size={14} aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
