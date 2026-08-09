/**
 * Minimal, dependency-free markdown → HTML renderer.
 *
 * Mirrors the legacy `simpleMarkdown` used by the report / technical-analysis
 * pages (headings, bold/italic/inline-code, pipe tables, list items), but
 * HTML-escapes the source first so LLM output cannot inject markup.
 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function renderMarkdown(md: string): string {
  const inline = escapeHtml(md)
    .replace(/^#### (.+)$/gm, '<h4 class="mt-4 mb-1 font-semibold">$1</h4>')
    .replace(/^### (.+)$/gm, '<h3 class="mt-5 mb-1.5 font-semibold">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="mt-6 mb-2 border-b border-border pb-1 text-lg font-semibold">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="mt-6 mb-2 text-xl font-semibold">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, '<code class="rounded bg-white/10 px-1 py-0.5 text-[0.9em]">$1</code>');

  const lines = inline.split("\n");
  const out: string[] = [];
  let inTable = false;

  for (const raw of lines) {
    const line = raw.trim();
    if (/^\|(.+)\|$/.test(line)) {
      if (/^\|[\s\-:|]+\|$/.test(line)) continue; // separator row
      const cells = line.split("|").filter((c) => c.trim() !== "");
      if (!inTable) {
        out.push('<div class="overflow-x-auto"><table class="my-2 w-full border-collapse text-sm">');
        out.push(
          "<tr>" +
            cells
              .map((c) => `<th class="border border-border bg-white/5 px-2.5 py-1.5 text-left">${c.trim()}</th>`)
              .join("") +
            "</tr>",
        );
        inTable = true;
      } else {
        out.push(
          "<tr>" +
            cells.map((c) => `<td class="border border-border px-2.5 py-1.5">${c.trim()}</td>`).join("") +
            "</tr>",
        );
      }
    } else {
      if (inTable) {
        out.push("</table></div>");
        inTable = false;
      }
      if (line === "" || line === "---") {
        out.push("<br>");
      } else if (line.startsWith("- ")) {
        out.push(`<p class="my-0.5 pl-4">• ${line.substring(2)}</p>`);
      } else {
        out.push(`<p class="my-1">${line}</p>`);
      }
    }
  }
  if (inTable) out.push("</table></div>");
  return out.join("\n");
}
