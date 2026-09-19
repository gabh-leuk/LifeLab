import { useState } from "react";
import { SparkleIcon, WarningIcon } from "@phosphor-icons/react";
import { Navigate } from "react-router-dom";
import { login, useAuth } from "../lib/auth";

// 演示账号的口令是**故意公开**的：它印在登录页上就是给人用的，不是秘密。
// 个人账号的口令不在这里，也不在仓库的任何文件里（只在用户脑子里）。
const DEMO_USER = "demo";
const DEMO_PASSWORD = "demo1234";

/** 登录页：内联面板，无模态（仓库里没有对话框这套东西）。 */
export default function LoginPage() {
  const { session, validating } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (validating) {
    return (
      <div className="page">
        <p className="muted">正在恢复会话…</p>
      </div>
    );
  }
  // 已登录（或刚登录成功）：回首页
  if (session) return <Navigate to="/" replace />;

  async function onLogin() {
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (e) {
      // request() 抛的 message 就是后端 detail
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form
        className="card login-card"
        onSubmit={(e) => {
          e.preventDefault();
          void onLogin();
        }}
      >
        <h1 className="brand-mark">LifeLab</h1>
        <p className="subtitle">个人行为实验 · 长期认知分析</p>

        <label className="field">
          <span>用户名</span>
          <input
            className="text-input"
            autoComplete="username"
            value={username}
            disabled={busy}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>

        <label className="field">
          <span>密码</span>
          <input
            className="text-input"
            type="password"
            autoComplete="current-password"
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error && (
          <div className="error" role="alert" onClick={() => setError(null)}>
            <WarningIcon size={14} aria-hidden="true" /> {error}
          </div>
        )}

        <button
          className="primary"
          type="submit"
          disabled={busy || !username.trim() || !password}
        >
          {busy ? "登录中…" : "登录"}
        </button>

        <p className="hint">不做自助注册：账号由后端脚本预置。</p>
      </form>

      {/* 给访客（比如面试官）的一条捷径：点一下就把口令填进表单，不用手打。
          放在 form 外面是为了不参与 submit 语义，也避免回车/密码管理器把它当表单元素。 */}
      <div className="card login-card demo-card">
        <h2 className="demo-title">
          <SparkleIcon size={15} weight="duotone" aria-hidden="true" />
          先随便看看？
        </h2>
        <button
          type="button"
          className="demo-fill"
          disabled={busy}
          onClick={() => {
            setUsername(DEMO_USER);
            setPassword(DEMO_PASSWORD);
            setError(null);
          }}
        >
          <code>
            {DEMO_USER} / {DEMO_PASSWORD}
          </code>
          <span className="demo-fill-cta">点此填入</span>
        </button>
        <p className="hint">
          演示账号可自由翻看与记录：事件、随想、状态都能写。只有 AI 生成不对它开放
          —— 那是真实模型调用，会被陌生人烧掉额度。预置的 33 条记忆可以直接看；
          「助手」页还留了 3 段预置会话，能看出「问数据 → 调用工具 → 确认后落库」
          是什么样子。
        </p>
      </div>
    </div>
  );
}
