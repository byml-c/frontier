const PROTECTED_SEGMENT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$(?!\$)[^$\n]*\$(?!\$)|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\))/g;
const INNER_BOUNDARY = /^[\s\u200B\u2060\uFEFF\u3000]+|[\s\u200B\u2060\uFEFF\u3000]+$/g;

function isEscaped(content: string, index: number): boolean {
  let backslashes = 0;
  for (let cursor = index - 1; cursor >= 0 && content[cursor] === "\\"; cursor -= 1) {
    backslashes += 1;
  }
  return backslashes % 2 === 1;
}

function findStrongDelimiters(line: string): number[] {
  const delimiters: number[] = [];
  for (let index = 0; index < line.length - 1; index += 1) {
    if (line[index] === "*" && line[index + 1] === "*" && isEscaped(line, index)) {
      // An escaped marker makes the intended pairing ambiguous. Leave this line
      // to the CommonMark parser instead of pairing a later closing marker as an
      // opening one.
      return [];
    }
    if (
      line[index] === "*"
      && line[index + 1] === "*"
      && line[index - 1] !== "*"
      && line[index + 2] !== "*"
      && !isEscaped(line, index)
    ) {
      delimiters.push(index);
      index += 1;
    }
  }
  return delimiters;
}

function normalizeStrongLine(line: string): string {
  const delimiters = findStrongDelimiters(line);
  if (delimiters.length < 2) return line;

  let cursor = 0;
  let normalized = "";
  for (let pairIndex = 0; pairIndex + 1 < delimiters.length; pairIndex += 2) {
    const opening = delimiters[pairIndex];
    const closing = delimiters[pairIndex + 1];
    const original = line.slice(opening, closing + 2);
    const value = line.slice(opening + 2, closing).replace(INNER_BOUNDARY, "");
    normalized += line.slice(cursor, opening);
    normalized += value ? `**${value}**` : original;
    cursor = closing + 2;
  }
  normalized += line.slice(cursor);
  return normalized;
}

function normalizeStrongSegment(segment: string): string {
  return segment.split("\n").map(normalizeStrongLine).join("\n");
}

/**
 * Repair CommonMark mistakes commonly produced by streamed model output while
 * leaving inline and fenced code untouched. Independent `**` delimiters are
 * paired from left to right on each line and their inner whitespace is ignored.
 * Repairs never cross a newline, so one unmatched delimiter cannot corrupt later
 * paragraphs.
 */
export function normalizeStrongMarkdown(content: string): string {
  return content.split(PROTECTED_SEGMENT).map((segment, index) => (
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
