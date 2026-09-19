import { useCallback, useEffect, useState } from "react";
import { PlusIcon, WarningIcon } from "@phosphor-icons/react";
import { api } from "../lib/api";
import type { Experiment, MetricDef } from "../lib/api";
import ExperimentCard from "../components/experiments/ExperimentCard";
import MetricEditor from "../components/experiments/MetricEditor";

const emptyForm = {
  name: "",
  question: "",
  hypothesis: "",
  variable: "",
  indicator: "",
  expected_days: "",
  baseline_note: "",
};

export default function ExperimentsPage() {
  const [list, setList] = useState<Experiment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [metrics, setMetrics] = useState<MetricDef[]>([]);
  // 还没定过来源的指标 key —— 原样提交给后端，只这些会被 AI 识别
  const [inferKeys, setInferKeys] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      setList(await api.experiments());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function submitCreate() {
    if (!form.name.trim()) return;
    setError(null);
    try {
      await api.createExperiment({
        name: form.name.trim(),
        question: form.question.trim() || undefined,
        hypothesis: form.hypothesis.trim() || undefined,
        variable: form.variable.trim() || undefined,
        indicator: form.indicator.trim() || undefined,
        metrics: metrics.length ? metrics : undefined,
        expected_days: form.expected_days ? Number(form.expected_days) : undefined,
        baseline_note: form.baseline_note.trim() || undefined,
        infer_keys: inferKeys,
      });
      setForm(emptyForm);
      setMetrics([]);
      setInferKeys([]);
      setCreating(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <div className="card-header">
        <h2>行为实验</h2>
        <button className="primary small" onClick={() => setCreating((c) => !c)}>
          {creating ? (
            "取消"
          ) : (
            <>
              <PlusIcon size={13} weight="bold" aria-hidden="true" /> 新实验
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="error" role="alert" onClick={() => setError(null)}>
          <WarningIcon size={14} aria-hidden="true" /> {error}
        </div>
      )}

      {creating && (
        <section className="card expand-enter">
          <h3>新实验</h3>
          <input
            className="text-input"
            placeholder="名称 *：如「短视频戒断实验」"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <textarea
            className="text-input tall"
            placeholder="问题：减少短视频以后，我的注意力是否改善？"
            value={form.question}
            onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))}
          />
          <textarea
            className="text-input tall"
            placeholder="假设：减少无限信息流可能提高注意力"
            value={form.hypothesis}
            onChange={(e) => setForm((f) => ({ ...f, hypothesis: e.target.value }))}
          />
          <textarea
            className="text-input tall"
            placeholder="改变什么（变量）：短视频使用时长"
            value={form.variable}
            onChange={(e) => setForm((f) => ({ ...f, variable: e.target.value }))}
          />
          <textarea
            className="text-input tall"
            placeholder="观察什么（指标，自由文本；结构化指标见下）"
            value={form.indicator}
            onChange={(e) => setForm((f) => ({ ...f, indicator: e.target.value }))}
          />

          <MetricEditor
            metrics={metrics}
            onChange={setMetrics}
            onInferKeysChange={setInferKeys}
          />

          <div className="log-form">
            <input
              className="text-input narrow"
              type="number"
              min={1}
              max={365}
              placeholder="预计周期(天)"
              value={form.expected_days}
              onChange={(e) => setForm((f) => ({ ...f, expected_days: e.target.value }))}
            />
            <input
              className="text-input"
              placeholder="基线说明：如「目前每天手机 4 小时」"
              value={form.baseline_note}
              onChange={(e) => setForm((f) => ({ ...f, baseline_note: e.target.value }))}
            />
          </div>

          <button className="primary" disabled={!form.name.trim()} onClick={submitCreate}>
            创建实验
          </button>
        </section>
      )}

      {list.length === 0 && !creating && (
        <p className="empty card">还没有实验，点右上角创建第一个。</p>
      )}

      {list.map((exp) => (
        <ExperimentCard
          key={exp.id}
          expId={exp.id}
          onChanged={load}
          onDeleted={load}
        />
      ))}
    </div>
  );
}
