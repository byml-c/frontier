const CODE_SEGMENT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;
const MALFORMED_OPENING_PAIR = /(?<![\\*])\*\*(?!\*)([\t \u00A0\u200B\u2060\u3000\uFEFF]+)([^\n]*?\S)[\t \u00A0\u200B\u2060\u3000\uFEFF]*\*\*(?!\*)/g;
const STRONG_PAIR_ON_LINE = /(?<![\\*])\*\*(?!\*)([^\n]+?)(?<![\\*])\*\*(?!\*)/g;
const STRONG_BOUNDARY = /(?<![\\*])(\*\*(?!\*)[^\n]+?(?<![\\*])\*\*)(?=[\p{L}\p{N}])/gu;
const INNER_BOUNDARY = /^[\s\u200B\u2060\uFEFF\u3000]+|[\s\u200B\u2060\uFEFF\u3000]+$/g;

function normalizeStrongSegment(segment: string): string {
  const repairedPairs: string[] = [];
  // Prefer the characteristic model error (`** label**`) before scanning normal
  // pairs. Placeholders prevent an unrelated, unmatched delimiter earlier in the
  // line from stealing this pair.
  const protectedSegment = segment.replace(
    MALFORMED_OPENING_PAIR,
    (_match, _leading: string, body: string) => {
      const marker = `\uE000frontier-strong-${repairedPairs.length}\uE001`;
      repairedPairs.push(`**${body.replace(INNER_BOUNDARY, "")}**`);
      return marker;
    },
  );
  const normalized = protectedSegment.replace(STRONG_PAIR_ON_LINE, (match, body: string) => {
    const value = body.replace(INNER_BOUNDARY, "");
    return value ? `**${value}**` : match;
  });
  return repairedPairs
    .reduce((value, pair, index) => value.replace(`\uE000frontier-strong-${index}\uE001`, pair), normalized)
    // A closing delimiter next to a CJK/Latin letter can fail CommonMark's
    // flanking rule when the bold text ends in punctuation.
    .replace(STRONG_BOUNDARY, "$1 ");
}

/**
 * Repair CommonMark mistakes commonly produced by streamed model output while
 * leaving inline and fenced code untouched. Repairs never cross a newline, so
 * one unmatched delimiter cannot corrupt later paragraphs.
 */
export function normalizeStrongMarkdown(content: string): string {
  return content.split(CODE_SEGMENT).map((segment, index) => (
    index % 2 === 1 ? segment : normalizeStrongSegment(segment)
  )).join("");
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
