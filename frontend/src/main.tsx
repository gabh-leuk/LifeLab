import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import RecordPage from "./pages/RecordPage";
import ExperimentsPage from "./pages/ExperimentsPage";
import LoginPage from "./pages/LoginPage";
import MemoryPage from "./pages/MemoryPage";
import AgentPage from "./pages/AgentPage";
import { ensureSession } from "./lib/auth";
import "./style.css";

// 有已存令牌就向后端校验回填；没登录则守卫会把人送到登录页。
// 事件类型清单等登录后再拉（见 RequireAuth）：未登录时拉只会拿到 401。
ensureSession();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route index element={<RecordPage />} />
            <Route path="/timeline" element={<Navigate to="/memory" replace />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
            <Route path="/knowledge" element={<Navigate to="/memory" replace />} />
            <Route path="/memory" element={<MemoryPage />} />
            {/* 复盘页已退役：生成与查看复盘都搬进了 Agent（见 M6 第二步） */}
            <Route path="/review" element={<Navigate to="/agent" replace />} />
            <Route path="/agent" element={<AgentPage />} />
          </Route>
        </Route>
      </Routes>
    </HashRouter>
  </React.StrictMode>,
);
