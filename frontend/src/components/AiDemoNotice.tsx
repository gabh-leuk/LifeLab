import { SparkleIcon } from "@phosphor-icons/react";
import { AI_DEMO_NOTICE } from "../lib/aiGate";

/** 演示账号下常显的说明：AI 生成按钮为什么是灰的（后端也会 403 拦下）。 */
export default function AiDemoNotice() {
  return (
    <p className="ai-demo-notice">
      <SparkleIcon size={13} weight="duotone" aria-hidden="true" />
      <span>{AI_DEMO_NOTICE}</span>
    </p>
  );
}
