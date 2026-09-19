import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChatCircleDotsIcon,
  CheckIcon,
  NotebookIcon,
  PlusIcon,
  SparkleIcon,
  WarningIcon,
  XIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { api } from "../lib/api";
import type { AgentThreadItem, AgentThreadState } from "../lib/api";
import { renderMarkdown } from "../lib/markdown";
import { useAiBlocked } from "../lib/aiGate";
import AiDemoNotice from "../components/AiDemoNotice";
import ReviewSection from "../components/agent/ReviewSection";

type TabKey = "chat" | "review";

const TABS: { key: TabKey; label: string; icon: Icon }[] = [
  { key: "chat", label: "对话", icon: ChatCircleDotsIcon },
  { key: "review", label: "复盘", icon: NotebookIcon },
];

const EXAMPLES = [
  "我上周的实验数据怎么样？",
  "帮我复盘一下昨天",
  "我现在有哪些长期问题？",
  "把刚才那条规律记下来",
];

/** 空会话时的能力卡片：先说清能做什么，点一下把话头填进输入框。
 *  用户大概率不会自己写 prompt，所以给的是带空位的模板而非空输入框。 */
const ABILITIES = [
  {
    title: "看数据",
    body: "实验跑到哪一步、最近有哪些规律、哪些长期问题还没动",
    template: "看一下最近 ____ 的数据（哪个实验、哪几天？）",
  },
  {
    title: "写知识库",
    body: "把一条觉察沉淀成记忆条目 —— 落库前弹确认卡，你点头才写",
    template: "把这条记下来：____",
  },
  {
    title: "生成复盘",
    body: "当场复盘某一天；日/周/月的历史都在「复盘」标签里翻",
    template: "复盘一下 ____（哪天？默认昨天）",
  },
];

function preview(value: unknown, max = 120): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? null);
  if (!text) return "—";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function argsPreview(args: Record<string, unknown>): string {
  const keys = Object.keys(args);
  if (keys.length === 0) return "";
  return keys.map((k) => `${k}=${preview(args[k], 28)}`).join(", ");
}

export default function AgentPage() {
  const aiBlocked = useAiBlocked();
  const [tab, setTab] = useState<TabKey>("chat");
  const [threads, setThreads] = useState<AgentThreadItem[]>([]);
  const [state, setState] = useState<AgentThreadState | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const pending = state?.pending ?? [];
  // 服务端的 args 覆盖是「一次覆盖本批全部待批调用」，所以只有单条时才给改
  const single = pending.length === 1 ? pending[0] : null;
  const waiting = pending.length > 0;

  const loadThreads = useCallback(async () => {
    try {
      setThreads(await api.agentThreads());
    } catch {
      /* 侧栏拉不动不该挡住对话 */
    }
  }, []);

  useEffect(() => {
    loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [state]);

  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : String(e));
  }

  async function send(text?: string) {
    const query = (text ?? input).trim();
    if (!query || busy || aiBlocked || waiting) return;
    setBusy(true);
    setError(null);
    setEditing(false);
    try {
      setState(await api.agentRun({ query, thread_id: state?.thread_id }));
      setInput("");
      loadThreads();
    } catch (e) {
      fail(e); // 输入框内容留着，免得重打
    } finally {
      setBusy(false);
    }
  }

  async function decide(approve: boolean) {
    if (!state || busy) return;
    let override: Record<string, unknown> | null = null;
    if (approve && editing && single) {
      try {
        override = JSON.parse(draft) as Record<string, unknown>;
      } catch {
        setError("参数不是合法 JSON；修正后再批准，或直接拒绝。");
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      setState(await api.agentResume({ thread_id: state.thread_id, approve, args: override }));
      setEditing(false);
      loadThreads();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function openThread(threadId: string) {
    if (busy || threadId === state?.thread_id) return;
    setBusy(true);
    setError(null);
    setEditing(false);
    try {
      setState(await api.agentThreadState(threadId));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  function newThread() {
    setState(null);
    setInput("");
    setEditing(false);
    setError(null);
  }

  function prefill(text: string) {
    setInput(text);
    inputRef.current?.focus();
  }

  const canSend = !aiBlocked && !busy && !waiting && input.trim().length > 0;

  return (
    <div className="page">
      <div className="card-header">
        <h2>
          <SparkleIcon size={19} weight="duotone" aria-hidden="true" /> 助手
        </h2>
        {tab === "chat" && (
          <button className="secondary small" onClick={newThread} disabled={busy || !state}>
            <PlusIcon size={13} aria-hidden="true" /> 新对话
          </button>
        )}
      </div>

      <div className="section-tabs">
        {TABS.map((t) => {
          const TabIcon = t.icon;
          return (
            <button
              key={t.key}
              className={"section-tab" + (tab === t.key ? " active" : "")}
              onClick={() => setTab(t.key)}
            >
              <TabIcon size={15} aria-hidden="true" /> {t.label}
            </button>
          );
        })}
      </div>

      {tab === "review" ? (
        <ReviewSection />
      ) : (
        <>
          <p className="hint">
            问记录、实验、长期问题都可以；<strong>写库会先弹确认卡</strong>
            ，你点头了才落库。<span className="muted"> 会话存在后端，刷新不丢。</span>
          </p>

          {error && (
            <div className="error" role="alert" onClick={() => setError(null)}>
              <WarningIcon size={14} aria-hidden="true" /> {error}（点击关闭）
            </div>
          )}

          <div className="agent-wrap">
            <aside className="agent-rail">
              <div className="agent-rail-head">
                <span className="agent-rail-label">会话</span>
              </div>
              {threads.length === 0 && <p className="hint">还没有会话</p>}
              {threads.map((t) => (
                <button
                  key={t.thread_id}
                  type="button"
                  className={
                    "agent-thread" + (t.thread_id === state?.thread_id ? " active" : "")
                  }
                  onClick={() => openThread(t.thread_id)}
                  title={t.title}
                >
                  {t.title || "新对话"}
                </button>
              ))}
            </aside>

            <section className="card agent-main">
              <div className="agent-stream">
                {!state && (
                  <div className="agent-intro">
                    <p className="agent-intro-lead">
                      我能读你的记录、实验和长期问题，也能把一条觉察写进知识库。
                      <span className="muted"> 下面三张卡片点一下会把话头填进输入框，你补上内容就能发。</span>
                    </p>
                    <div className="agent-abilities">
                      {ABILITIES.map((a) => (
                        <button
                          key={a.title}
                          type="button"
                          className="agent-ability"
                          disabled={busy || aiBlocked || waiting}
                          onClick={() => prefill(a.template)}
                        >
                          <strong>{a.title}</strong>
                          <span>{a.body}</span>
                        </button>
                      ))}
                    </div>
                    <div className="agent-examples">
                      {EXAMPLES.map((q) => (
                        <button
                          key={q}
                          type="button"
                          className="secondary small"
                          disabled={busy || aiBlocked || waiting}
                          onClick={() => send(q)}
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {(state?.messages ?? []).map((m, i) =>
                  m.role === "user" ? (
                    <div key={i} className="agent-bubble user">
                      {m.content}
                    </div>
                  ) : (
                    <div
                      key={i}
                      className="agent-bubble markdown-body"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
                    />
                  ),
                )}
                <div ref={bottomRef} />
              </div>

              {state && state.steps.length > 0 && (
                <details className="agent-steps">
                  <summary>工具调用 · {state.steps.length} 次</summary>
                  <ol>
                    {state.steps.map((step, i) => (
                      <li key={i}>
                        <code>
                          {step.tool}({argsPreview(step.args)})
                        </code>
                        {" → "}
                        {step.approved === false ? (
                          <span className="agent-denied">已拒绝，未落库</span>
                        ) : (
                          <span>{preview(step.result)}</span>
                        )}
                        {step.approved === true && <span className="muted"> · 已确认落库</span>}
                      </li>
                    ))}
                  </ol>
                </details>
              )}

              {pending.length > 0 && (
                <div className="candidate agent-pending">
                  <div className="candidate-body">
                    <strong>这条要写进知识库，确认吗？</strong>
                    {pending.map((call) => (
                      <p key={call.id} className="candidate-note">
                        {call.summary}
                      </p>
                    ))}
                    {single && editing ? (
                      <>
                        <textarea
                          className="text-input tall"
                          value={draft}
                          spellCheck={false}
                          onChange={(e) => setDraft(e.target.value)}
                          aria-label="待写入的参数（JSON）"
                        />
                        <p className="hint">改完再点批准；服务端会重新校验一遍参数。</p>
                      </>
                    ) : (
                      <pre className="agent-args">{JSON.stringify(single?.args ?? pending[0].args, null, 2)}</pre>
                    )}
                    <div className="candidate-meta">
                      <button
                        className="primary small"
                        disabled={busy}
                        onClick={() => decide(true)}
                      >
                        <CheckIcon size={13} aria-hidden="true" /> 批准并记录
                      </button>
                      <button
                        className="secondary small"
                        disabled={busy}
                        onClick={() => decide(false)}
                      >
                        <XIcon size={13} aria-hidden="true" /> 拒绝
                      </button>
                      {single && !editing && (
                        <button
                          className="secondary small"
                          disabled={busy}
                          onClick={() => {
                            setDraft(JSON.stringify(single.args, null, 2));
                            setEditing(true);
                          }}
                        >
                          改参数
                        </button>
                      )}
                    </div>
                    {!editing && (
                      <p className="candidate-note">
                        批准后才会写库；拒绝则这次不写，对话照常继续。
                      </p>
                    )}
                  </div>
                </div>
              )}

              <div className="agent-compose">
                {aiBlocked && <AiDemoNotice />}
                <div className="agent-input-row">
                  <textarea
                    className="text-input tall"
                    placeholder={
                      aiBlocked
                        ? "演示账号下助手不可用"
                        : waiting
                          ? "先处理上面的确认卡，再继续聊"
                          : "问点什么，或说「把这条记下来」…（Enter 发送，Shift+Enter 换行）"
                    }
                    value={input}
                    ref={inputRef}
                    disabled={aiBlocked || busy || waiting}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        send();
                      }
                    }}
                    aria-label="给助手发消息"
                  />
                  <button className="primary" disabled={!canSend} onClick={() => send()}>
                    {busy ? "处理中…" : "发送"}
                  </button>
                </div>
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
