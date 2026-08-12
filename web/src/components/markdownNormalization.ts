const CODE_SEGMENT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;
const STRONG_PAIR = /(?<![\\*])\*\*(?!\*)([\s\S]+?)(?<![\\*])\*\*(?!\*)/g;
const STRONG_BOUNDARY = /(?<![\\*])(\*\*(?!\*)[\s\S]+?(?<![\\*])\*\*)(?=[\p{L}\p{N}])/gu;
const INNER_BOUNDARY = /^[\s\u200B\u2060\uFEFF\u3000]+|[\s\u200B\u2060\uFEFF\u3000]+$/g;

/**
 * Repair the small CommonMark mistakes that streamed model output commonly
 * contains, while leaving inline and fenced code untouched.
 */
export function normalizeStrongMarkdown(content: string): string {
  return content.split(CODE_SEGMENT).map((segment, index) => {
    if (index % 2 === 1) return segment;
    return segment
      .replace(STRONG_PAIR, (match, body: string) => {
        const leading = /^[\s\u200B\u2060\uFEFF\u3000]+/.test(body) ? " " : "";
        const trailing = /[\s\u200B\u2060\uFEFF\u3000]+$/.test(body) ? " " : "";
        const normalized = body.replace(INNER_BOUNDARY, "");
        return normalized ? `${leading}**${normalized}**${trailing}` : match;
      })
      // `**重点。**下一句` is not a valid closing delimiter in CommonMark.
      // A normal space preserves the visual sentence while making it parseable.
      .replace(STRONG_BOUNDARY, "$1 ");
  }).join("");
}

export function prepareMarkdown(content: string): string {
  return normalizeStrongMarkdown(content)
    .replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, body: string) => `$$\n${body}\n$$`)
    .replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, body: string) => `$${body}$`)
    .replace(
      /\[\[(?:page|p)[:. ]?(\d+(?:\s*[-–—~～]\s*\d+)?(?:\s*[,，]\s*\d+(?:\s*[-–—~～]\s*\d+)?)*)(?:\|([^\]]+))?\]\]/gi,
      (_match, pageSpec: string, label?: string) => {
        const references = pageSpec.split(/\s*[,，]\s*/).map((part) => {
          const pageMatch = part.match(/^(\d+)(?:\s*[-–—~～]\s*(\d+))?$/);
          if (!pageMatch) return null;
          const [, startPage, endPage] = pageMatch;
          const range = endPage ? `${startPage}–${endPage}` : startPage;
          return { range, href: `#frontier-page-${startPage}${endPage ? `-${endPage}` : ""}` };
        }).filter((item): item is { range: string; href: string } => item !== null);
        if (references.length === 1 && label) return `[${label}](${references[0].href})`;
        const links = references.map((item) => `[第 ${item.range} 页](${item.href})`).join("、");
        return label ? `${label}（${links}）` : links;
      },
    );
}
