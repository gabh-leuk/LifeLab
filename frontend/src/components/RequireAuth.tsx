import { useEffect } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ensureEventTypes } from "../lib/eventTypes";

/** 未登录 → 登录页；首屏回填令牌期间先占位，免得闪一下登录页。 */
export default function RequireAuth() {
  const { session, validating } = useAuth();

  // 事件类型清单（内置 + 自定义）全站共用；登录后再拉，内部有一次性哨兵
  useEffect(() => {
    if (session) ensureEventTypes();
  }, [session]);

  if (validating) {
    return (
      <div className="page">
        <p className="muted">正在恢复会话…</p>
      </div>
    );
  }
  if (!session) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
