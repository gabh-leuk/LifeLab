import { useState } from "react";
import { MoonIcon, SunDimIcon } from "@phosphor-icons/react";
import { NavLink, Outlet } from "react-router-dom";
import { logout, useAuth } from "../lib/auth";
import { getTheme, toggleTheme } from "../lib/theme";

const navItems = [
  { to: "/", label: "记录", end: true },
  { to: "/memory", label: "记忆" },
  { to: "/experiments", label: "实验" },
  { to: "/agent", label: "助手" },
];

export default function Layout() {
  const [theme, setThemeState] = useState(getTheme);
  const { session } = useAuth();

  function onToggleTheme() {
    setThemeState(toggleTheme());
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <header className="site-header">
        <div className="brand">
          <h1 className="brand-mark">LifeLab</h1>
          <p className="subtitle">个人行为实验 · 长期认知分析</p>
        </div>
        <div className="header-side">
          {session && (
            <span className="session-user" title={`@${session.user.username}`}>
              {session.user.display_name || session.user.username}
              {session.user.is_demo && <span className="badge">演示</span>}
            </span>
          )}
          <button
            className="theme-toggle"
            onClick={onToggleTheme}
            aria-label={theme === "dark" ? "切换到日间模式" : "切换到夜间模式"}
          >
            {theme === "dark" ? (
              <SunDimIcon size={15} aria-hidden="true" />
            ) : (
              <MoonIcon size={15} aria-hidden="true" />
            )}
            {theme === "dark" ? "日间" : "夜间"}
          </button>
          {session && (
            <button className="theme-toggle" onClick={() => void logout()}>
              退出
            </button>
          )}
        </div>
      </header>
      <nav className="nav" aria-label="主导航">
        {navItems.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
      <main id="main">
        <Outlet />
      </main>
    </div>
  );
}
