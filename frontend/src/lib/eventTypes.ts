/**
 * 事件类型注册表：启动拉一次 `/event-types`，全站共享。
 *
 * 为什么要注册表：历史事件里的 `type` 可能是用户自定义 key（`custom_<8hex>`），
 * 光靠内置的 `EVENT_LABELS` / `EVENT_ICONS` 只能显示裸 key。
 * 内置类型的标签/图标仍以内置表为准（手挑的图标比按大类兜底好看）。
 */

import type { Icon } from "@phosphor-icons/react";
import { useMemo, useSyncExternalStore } from "react";
import { api, type EventTypeItem } from "./api";
import { EVENT_TYPES, eventTypeLabel } from "./constants";
import { categoryIcon, eventIcon, iconForCustom } from "./icons";

// 首屏兜底：内置可记录项（与后端 creatable_builtins 一致，不含在线/采集类型）。
// 后端清单到达前先把按钮渲染出来，避免网格空闪。
// category 留空——内置项在管理面板里只读，图标走 EVENT_ICONS，用不到它。
const FALLBACK: EventTypeItem[] = EVENT_TYPES.map((t, i) => ({
  key: t.key,
  label: t.label,
  category: "",
  icon: null,
  order: i,
  id: null,
  builtin: true,
  archived: false,
}));

let all: EventTypeItem[] = FALLBACK;
let active: EventTypeItem[] = FALLBACK;
// 懒加载哨兵：失败时置回 false，下次挂载再试
let requested = false;
const listeners = new Set<() => void>();

function setItems(rows: EventTypeItem[]) {
  all = rows;
  active = rows.filter((r) => !r.archived);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 拉取并广播（增删改后调用） */
export function refreshEventTypes(): Promise<void> {
  return api.listEventTypes().then(setItems);
}

/** 启动时调用一次；不阻塞渲染，拿到数据后自动重渲染订阅者 */
export function ensureEventTypes(): void {
  if (requested) return;
  requested = true;
  refreshEventTypes().catch(() => {
    requested = false;
  });
}

/** 可记录的类型（不含已归档），记录页按钮即此顺序 */
export function useEventTypes(): EventTypeItem[] {
  return useSyncExternalStore(subscribe, () => active);
}

/** 把 type 解析成中文名与图标（含归档类型，历史记录才能正常显示） */
export function useEventTypeResolver(): {
  label: (type: string) => string;
  icon: (type: string) => Icon;
} {
  const items = useSyncExternalStore(subscribe, () => all);
  return useMemo(() => {
    const byKey = new Map(items.map((i) => [i.key, i]));
    return {
      label: (type: string) => byKey.get(type)?.label ?? eventTypeLabel(type),
      icon: (type: string) => {
        const hit = byKey.get(type);
        // 内置项走内置图标表：手挑的图标优先于按大类兜底
        if (!hit || hit.builtin) return eventIcon(type);
        return hit.icon ? iconForCustom(hit.icon) : categoryIcon(hit.category);
      },
    };
  }, [items]);
}
