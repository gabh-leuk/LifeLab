import { useState } from "react";
import {
  BrainIcon,
  TargetIcon,
  TimerIcon,
  UserCircleIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import ExperienceSection from "../components/memory/ExperienceSection";
import KnowledgeSection from "../components/memory/KnowledgeSection";
import ProfileSection from "../components/memory/ProfileSection";

type SectionKey = "experience" | "profile" | "knowledge";

const SECTIONS: { key: SectionKey; label: string; icon: Icon }[] = [
  { key: "experience", label: "个人经历", icon: TimerIcon },
  { key: "profile", label: "用户背景", icon: UserCircleIcon },
  { key: "knowledge", label: "问题与发现", icon: TargetIcon },
];

export default function MemoryPage() {
  const [section, setSection] = useState<SectionKey>("experience");

  return (
    <div className="page">
      <div className="card-header">
        <h2>
          <BrainIcon size={19} weight="duotone" aria-hidden="true" /> 记忆
        </h2>
      </div>

      <div className="section-tabs">
        {SECTIONS.map((s) => {
          const SectionIcon = s.icon;
          return (
            <button
              key={s.key}
              className={"section-tab" + (section === s.key ? " active" : "")}
              onClick={() => setSection(s.key)}
            >
              <SectionIcon size={15} aria-hidden="true" /> {s.label}
            </button>
          );
        })}
      </div>

      {/* key 触发重挂载 → 分区切换时播放轻微过渡 */}
      <div key={section} className="enter-fade">
        {section === "experience" && <ExperienceSection />}
        {section === "profile" && <ProfileSection />}
        {section === "knowledge" && <KnowledgeSection />}
      </div>
    </div>
  );
}
