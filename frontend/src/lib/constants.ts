// 记录按钮：只保留设备无法观测的「线下行为」；在线使用（含游戏）由采集自动生成。
export const EVENT_TYPES = [
  { key: "LEARNING_START", label: "学习" },
  { key: "MEAL_START", label: "吃饭" },
  { key: "BED_START", label: "上床" },
  { key: "OUT_START", label: "出门" },
  { key: "EXERCISE_START", label: "运动" },
  { key: "CHORES_START", label: "家务" },
  { key: "SOCIAL_OFFLINE_START", label: "线下社交" },
  { key: "OTHER_START", label: "其他" },
] as const;

// 全部事件类型的中文名（含历史/采集类型，供时间轴展示）
export const EVENT_LABELS: Record<string, string> = {
  LEARNING_START: "学习",
  GAME_START: "游戏",
  PHONE_START: "手机",
  MEAL_START: "吃饭",
  BED_START: "上床",
  SLEEP_START: "上床",
  OUT_START: "出门",
  EXERCISE_START: "运动",
  CHORES_START: "家务",
  SOCIAL_OFFLINE_START: "线下社交",
  OTHER_START: "其他",
  DEVICE_ACTIVITY: "设备使用",
};

export function eventTypeLabel(type: string): string {
  return EVENT_LABELS[type] ?? type.replace(/_START$/, "");
}

// 行为大类（与后端 app/behavior_categories.py 一致）
export const BEHAVIOR_CATEGORIES = [
  { key: "study_work", label: "学习工作", online: true },
  { key: "video", label: "视频", online: true },
  { key: "game", label: "游戏", online: true },
  { key: "social", label: "社交", online: true },
  { key: "reading", label: "阅读", online: true },
  { key: "audio", label: "音频", online: true },
  { key: "shopping", label: "购物", online: true },
  { key: "browsing", label: "浏览", online: true },
  { key: "creation", label: "创作", online: true },
  { key: "utility", label: "工具系统", online: true },
  { key: "finance", label: "金融理财", online: true },
  { key: "life_service", label: "生活服务", online: true },
  { key: "forum", label: "论坛社区", online: true },
  { key: "other_online", label: "其他在线", online: true },
  { key: "bed", label: "上床", online: false },
  { key: "meal", label: "吃饭", online: false },
  { key: "exercise", label: "运动", online: false },
  { key: "commute", label: "出门通勤", online: false },
  { key: "chores", label: "家务", online: false },
  { key: "social_offline", label: "线下社交", online: false },
  { key: "other", label: "其他", online: false },
] as const;

export function categoryLabel(code?: string | null): string {
  if (!code) return "";
  return BEHAVIOR_CATEGORIES.find((c) => c.key === code)?.label ?? code;
}

export const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  RUNNING: "进行中",
  PAUSED: "已暂停",
  COMPLETED: "已完成",
  CONCLUDED: "已下结论",
};

// 指标数据源：manual 手动录入；其余从日常记录/设备采集自动聚合
export const METRIC_SOURCES = [
  { value: "manual", label: "手动录入" },
  { value: "event_duration:LEARNING_START", label: "自动：学习时长(分钟/天)" },
  { value: "event_count:LEARNING_START", label: "自动：学习次数(次/天)" },
  { value: "event_duration:EXERCISE_START", label: "自动：运动时长(分钟/天)" },
  { value: "event_count:EXERCISE_START", label: "自动：运动次数(次/天)" },
  { value: "state_avg:energy", label: "自动：日均精力" },
  { value: "state_avg:focus", label: "自动：日均专注" },
  { value: "state_avg:irritation", label: "自动：日均烦躁" },
  { value: "usage_total", label: "采集：设备使用总时长(分钟/天)" },
  { value: "usage_platform:android", label: "采集：手机使用时长(分钟/天)" },
  { value: "usage_platform:pc", label: "采集：电脑使用时长(分钟/天)" },
  { value: "usage_platform:android@22-24", label: "采集：手机 22-24 点使用(分钟/天)" },
  { value: "usage_platform:pc@22-24", label: "采集：电脑 22-24 点使用(分钟/天)" },
  { value: "usage_platform:android@0-6", label: "采集：手机 0-6 点使用(分钟/天)" },
  // 按行为大类（设备真实活动，自动生成的时间段）
  { value: "event_duration_category:video", label: "采集：视频时长(分钟/天)" },
  { value: "event_duration_category:study_work", label: "采集：学习工作时长(分钟/天)" },
  { value: "event_duration_category:game", label: "采集：游戏时长(分钟/天)" },
  { value: "event_duration_category:social", label: "采集：社交时长(分钟/天)" },
  { value: "event_count_category:video", label: "采集：视频段数(次/天)" },
] as const;

export function metricSourceLabel(source?: string): string {
  if (!source || source === "manual") return "手动";
  return METRIC_SOURCES.find((s) => s.value === source)?.label ?? source;
}

// 用户背景分类（画像层：跨话题稳定的自我描述）
export const PROFILE_CATEGORIES = [
  { key: "identity", label: "身份" },
  { key: "habit", label: "习惯" },
  { key: "preference", label: "偏好" },
  { key: "context", label: "情境" },
  { key: "constraint", label: "限制" },
] as const;

export function profileCategoryLabel(category: string): string {
  return (
    PROFILE_CATEGORIES.find((c) => c.key === category)?.label ?? category
  );
}
