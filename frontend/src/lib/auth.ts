/**
 * 会话状态：登录令牌 + 当前用户，全站共享。
 *
 * 与 eventTypes.ts 同构（模块级变量 + 监听者集合 + useSyncExternalStore），
 * 不另起 Context/store——仓库里只有这一种全局状态写法。
 * useSyncExternalStore 要求快照引用稳定，所以每次变更整体换对象。
 */

import { useSyncExternalStore } from "react";
import { api, setAuthHeaderToken, setUnauthorizedHandler, type User } from "./api";

const STORAGE_KEY = "lifelab-session";

export interface Session {
  token: string;
  user: User;
}

export interface AuthState {
  session: Session | null;
  /** 正拿已存的令牌向后端校验：守卫靠它区分「未登录」与「校验中」 */
  validating: boolean;
}

function readStored(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    return parsed?.token && parsed?.user ? parsed : null;
  } catch {
    return null; // 隐私模式 / 脏数据
  }
}

const stored = readStored();
let state: AuthState = { session: stored, validating: stored !== null };
let checked = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function setSession(next: Session | null) {
  state = { session: next, validating: false };
  setAuthHeaderToken(next?.token ?? null);
  try {
    if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* 隐私模式等场景忽略 */
  }
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 令牌被撤销/过期时由 api.ts 的 401 钩子调用。并发请求会多次触发，故幂等。 */
export function clearSession(): void {
  if (state.session === null) return;
  setSession(null);
}

setAuthHeaderToken(state.session?.token ?? null);
setUnauthorizedHandler(clearSession);

/** 启动时调用一次：用已存的令牌回填用户（它可能在别处被撤销）。 */
export function ensureSession(): void {
  if (checked) return;
  checked = true;
  const current = state.session;
  if (!current) return;
  api
    .me()
    .then((user) => setSession({ token: current.token, user }))
    .catch(() => clearSession());
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, () => state);
}

export async function login(username: string, password: string): Promise<void> {
  const res = await api.login(username, password);
  setSession({ token: res.token, user: res.user });
}

export async function logout(): Promise<void> {
  try {
    await api.logout(); // 令牌可能已失效；登出始终以本地为准
  } catch {
    /* 忽略 */
  }
  setSession(null);
}
