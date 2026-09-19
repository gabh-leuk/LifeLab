export type Theme = "light" | "dark";

const STORAGE_KEY = "lifelab-theme";

export function getTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  return attr === "dark" ? "dark" : "light";
}

export function setTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}
