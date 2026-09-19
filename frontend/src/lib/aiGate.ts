import { useAuth } from "./auth";

/**
 * 与后端 `app/deps.py` 的 `AI_DEMO_NOTICE` 是**同一句话**（后端 403 的 detail 也用它）。
 *
 * 后端那份是不可绕过的真闸门，这份只负责解释「按钮为什么是灰的」——
 * 所以两者措辞必须一致，改一处就得改另一处。
 */
export const AI_DEMO_NOTICE =
  "演示账号不开放 AI 生成：这是公共 demo，AI 生成会消耗开发者额度。" +
  "预置的 10 篇日报 / 2 篇周报 / 1 篇月报 / 33 条记忆可直接查看。";

/** 当前会话是否处于演示账号 —— 这些账号的 AI 生成已被后端封锁。 */
export function useAiBlocked(): boolean {
  return useAuth().session?.user.is_demo === true;
}
