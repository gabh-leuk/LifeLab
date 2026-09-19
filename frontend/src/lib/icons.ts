import type { Icon } from "@phosphor-icons/react";
import {
  AirplaneTiltIcon,
  ArmchairIcon,
  BarbellIcon,
  BedIcon,
  BookOpenIcon,
  BowlFoodIcon,
  BrainIcon,
  BriefcaseIcon,
  BusIcon,
  CameraIcon,
  CardsThreeIcon,
  ChatCircleDotsIcon,
  ChatsIcon,
  CheckCircleIcon,
  CodeIcon,
  CoffeeIcon,
  CookingPotIcon,
  DevicesIcon,
  DoorIcon,
  FilmStripIcon,
  FireIcon,
  FlaskIcon,
  FlowerLotusIcon,
  ForkKnifeIcon,
  GameControllerIcon,
  GuitarIcon,
  HeartbeatIcon,
  HourglassMediumIcon,
  HouseLineIcon,
  LightbulbIcon,
  MagnifyingGlassIcon,
  MicrophoneIcon,
  MoneyIcon,
  MoonStarsIcon,
  MountainsIcon,
  MusicNotesIcon,
  NotebookIcon,
  PaintBrushIcon,
  PaletteIcon,
  PauseIcon,
  PencilLineIcon,
  PersonSimpleBikeIcon,
  PersonSimpleRunIcon,
  PersonSimpleWalkIcon,
  PlanetIcon,
  PlantIcon,
  QuestionIcon,
  ReceiptIcon,
  RecycleIcon,
  ShieldCheckIcon,
  ShoppingCartIcon,
  SparkleIcon,
  StampIcon,
  StudentIcon,
  SunIcon,
  TargetIcon,
  TeaBagIcon,
  TerminalWindowIcon,
  TimerIcon,
  TranslateIcon,
  UsersIcon,
  WalletIcon,
  WaveformIcon,
  WrenchIcon,
  XCircleIcon,
} from "@phosphor-icons/react";

/** 事件类型图标（替代 emoji，风格统一、跨平台一致） */
export const EVENT_ICONS: Record<string, Icon> = {
  LEARNING_START: BookOpenIcon,
  GAME_START: GameControllerIcon,
  PHONE_START: WaveformIcon,
  MEAL_START: CookingPotIcon,
  BED_START: BedIcon,
  SLEEP_START: BedIcon,
  OUT_START: DoorIcon,
  EXERCISE_START: BarbellIcon,
  CHORES_START: WrenchIcon,
  SOCIAL_OFFLINE_START: UsersIcon,
  OTHER_START: PlanetIcon,
  DEVICE_ACTIVITY: DevicesIcon,
};

export function eventIcon(type: string): Icon {
  return EVENT_ICONS[type] ?? PlanetIcon;
}

/** 自定义类型可选图标（精选集，不给用户填任意图标名） */
export const ICON_CHOICES: { key: string; label: string; Icon: Icon }[] = [
  { key: "FlowerLotus", label: "冥想", Icon: FlowerLotusIcon },
  { key: "PencilLine", label: "写作", Icon: PencilLineIcon },
  { key: "BookOpen", label: "阅读", Icon: BookOpenIcon },
  { key: "Notebook", label: "笔记", Icon: NotebookIcon },
  { key: "Code", label: "写代码", Icon: CodeIcon },
  { key: "TerminalWindow", label: "终端", Icon: TerminalWindowIcon },
  { key: "Flask", label: "研究", Icon: FlaskIcon },
  { key: "Student", label: "功课", Icon: StudentIcon },
  { key: "Briefcase", label: "工作", Icon: BriefcaseIcon },
  { key: "Translate", label: "语言", Icon: TranslateIcon },
  { key: "Barbell", label: "健身", Icon: BarbellIcon },
  { key: "PersonSimpleRun", label: "跑步", Icon: PersonSimpleRunIcon },
  { key: "PersonSimpleBike", label: "骑行", Icon: PersonSimpleBikeIcon },
  { key: "PersonSimpleWalk", label: "散步", Icon: PersonSimpleWalkIcon },
  { key: "Mountains", label: "户外", Icon: MountainsIcon },
  { key: "PaintBrush", label: "画画", Icon: PaintBrushIcon },
  { key: "Palette", label: "设计", Icon: PaletteIcon },
  { key: "Camera", label: "拍照", Icon: CameraIcon },
  { key: "MusicNotes", label: "音乐", Icon: MusicNotesIcon },
  { key: "Guitar", label: "乐器", Icon: GuitarIcon },
  { key: "Microphone", label: "录音", Icon: MicrophoneIcon },
  { key: "FilmStrip", label: "看片", Icon: FilmStripIcon },
  { key: "ForkKnife", label: "做饭", Icon: ForkKnifeIcon },
  { key: "BowlFood", label: "饮食", Icon: BowlFoodIcon },
  { key: "Coffee", label: "咖啡", Icon: CoffeeIcon },
  { key: "TeaBag", label: "喝茶", Icon: TeaBagIcon },
  { key: "ShoppingCart", label: "购物", Icon: ShoppingCartIcon },
  { key: "Wallet", label: "记账", Icon: WalletIcon },
  { key: "Money", label: "花钱", Icon: MoneyIcon },
  { key: "ChatCircleDots", label: "聊天", Icon: ChatCircleDotsIcon },
  { key: "Users", label: "社交", Icon: UsersIcon },
  { key: "HouseLine", label: "居家", Icon: HouseLineIcon },
  { key: "Bus", label: "通勤", Icon: BusIcon },
  { key: "AirplaneTilt", label: "旅行", Icon: AirplaneTiltIcon },
  { key: "Heartbeat", label: "健康", Icon: HeartbeatIcon },
  { key: "Brain", label: "脑力", Icon: BrainIcon },
  { key: "Target", label: "目标", Icon: TargetIcon },
  { key: "Timer", label: "计时", Icon: TimerIcon },
  { key: "MoonStars", label: "睡前", Icon: MoonStarsIcon },
  { key: "Sun", label: "早起", Icon: SunIcon },
  { key: "Armchair", label: "休息", Icon: ArmchairIcon },
  { key: "Plant", label: "植物", Icon: PlantIcon },
  { key: "Fire", label: "打卡", Icon: FireIcon },
  { key: "Wrench", label: "折腾", Icon: WrenchIcon },
  { key: "GameController", label: "游戏", Icon: GameControllerIcon },
  { key: "Devices", label: "设备", Icon: DevicesIcon },
];

const ICON_CHOICE_MAP: Record<string, Icon> = Object.fromEntries(
  ICON_CHOICES.map((c) => [c.key, c.Icon]),
);

/** 自定义类型没选图标时，按行为大类给个默认（不放大类语义的落 Planet） */
const CATEGORY_ICONS: Record<string, Icon> = {
  study_work: BookOpenIcon,
  video: FilmStripIcon,
  game: GameControllerIcon,
  social: ChatCircleDotsIcon,
  reading: BookOpenIcon,
  audio: MusicNotesIcon,
  shopping: ShoppingCartIcon,
  browsing: MagnifyingGlassIcon,
  creation: PaintBrushIcon,
  utility: WrenchIcon,
  finance: MoneyIcon,
  life_service: ReceiptIcon,
  forum: ChatsIcon,
  other_online: DevicesIcon,
  bed: BedIcon,
  meal: CookingPotIcon,
  exercise: BarbellIcon,
  commute: DoorIcon,
  chores: HouseLineIcon,
  social_offline: UsersIcon,
  other: PlanetIcon,
};

export function iconForCustom(key?: string | null): Icon {
  return (key && ICON_CHOICE_MAP[key]) || PlanetIcon;
}

export function categoryIcon(category?: string | null): Icon {
  return (category && CATEGORY_ICONS[category]) || PlanetIcon;
}

/** 记忆条目类型图标 */
export const MEMORY_KIND_ICONS: Record<string, Icon> = {
  thought: LightbulbIcon,
  event: CardsThreeIcon,
  review: NotebookIcon,
  finding: MagnifyingGlassIcon,
  problem: TargetIcon,
  insight: SparkleIcon,
  pattern: RecycleIcon,
};

export function memoryKindIcon(kind: string): Icon {
  return MEMORY_KIND_ICONS[kind] ?? CardsThreeIcon;
}

/** 实验判定图标 */
export const VERDICT_ICONS: Record<string, Icon> = {
  SUPPORTED: CheckCircleIcon,
  PARTIALLY_SUPPORTED: HourglassMediumIcon,
  REFUTED: XCircleIcon,
  INCONCLUSIVE: QuestionIcon,
};

/** 其他语义图标统一出口，避免各组件重复引入 */
export const ICONS = {
  idea: LightbulbIcon,
  state: BarbellIcon,
  confirm: ShieldCheckIcon,
  stamp: StampIcon,
  timer: TimerIcon,
  plant: PlantIcon,
  pause: PauseIcon,
  devices: DevicesIcon,
} as const;
