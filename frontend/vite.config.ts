import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 监听所有网卡（手机经 Tailscale IP 访问前端）
    port: 5173,
  },
});
