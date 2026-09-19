/** 极简 markdown 渲染（标题 / 列表 / 分隔线）。
 *
 * 对话气泡与复盘正文共用。刻意不引第三方：只支持我们自己生成的这几种结构。
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 先转义再拼标签 —— 回答与复盘里都会夹带用户自己的记录内容。 */
export function renderMarkdown(md: string): string {
  return md
    .split("\n")
    .map((line) => {
      const t = escapeHtml(line.trim());
      if (t.startsWith("### ")) return `<h4>${t.slice(4)}</h4>`;
      if (t.startsWith("## ")) return `<h3>${t.slice(3)}</h3>`;
      if (t.startsWith("- ") || t.startsWith("* ")) return `<li>${t.slice(2)}</li>`;
      if (t === "---") return `<hr/>`;
      if (t) return `<p>${t}</p>`;
      return "";
    })
    .join("");
}
