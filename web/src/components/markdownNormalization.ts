const PROTECTED_SEGMENT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\$\$[\s\S]*?\$\$|\$(?!\$)[^$\n]*\$(?!\$)|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\))/g;
const DISPLAY_LATEX_MATH = /^([ \t]*(?:>[ \t]*)*(?:(?:[-+*]|\d+[.)])[ \t]+)?[ \t]*)\\\[[ \t]*\r?\n([\s\S]*?)^([ \t]*(?:>[ \t]*)*[ \t]*)\\\][ \t]*$/gm;
const SINGLE_LINE_DISPLAY_LATEX_MATH = /^([ \t]*(?:>[ \t]*)*)(([-+*]|\d+[.)])([ \t]+))?([ \t]*)\\\[([^\r\n]*?)\\\][ \t]*\r?$/gm;
const FENCE_OPENING = /^([ \t]*(?:>[ \t]*)*(?:(?:[-+*]|\d+[.)])[ \t]+)?[ \t]*)(`{3,}|~{3,})/;
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

function normalizeLatexMathSegment(segment: string): string {
  return segment
    .replace(
      DISPLAY_LATEX_MATH,
      (_match, openingPrefix: string, body: string, closingPrefix: string) => (
        `${openingPrefix}$$\n${body}${closingPrefix}$$`
      ),
    )
    .replace(
      SINGLE_LINE_DISPLAY_LATEX_MATH,
      (
        _match,
        containerPrefix: string,
        listPrefix: string | undefined,
        listMarker: string | undefined,
        listSpacing: string | undefined,
        indentation: string,
        body: string,
      ) => {
        const openingPrefix = `${containerPrefix}${listPrefix ?? ""}${indentation}`;
        const continuationPrefix = listMarker
          ? `${containerPrefix}${" ".repeat(listMarker.length + (listSpacing?.length ?? 0))}${indentation}`
          : `${containerPrefix}${indentation}`;
        return `${openingPrefix}$$\n${continuationPrefix}${body}\n${continuationPrefix}$$`;
      },
    )
    .replace(/(?<!\\)\\\(([^\r\n]*?)(?<!\\)\\\)/g, (_match, body: string) => `$${body}$`);
}

function fenceEnd(content: string, openingEnd: number, marker: string): number {
  const markerCharacter = marker[0];
  let lineStart = content.indexOf("\n", openingEnd);
  if (lineStart < 0) return content.length;
  lineStart += 1;
  while (lineStart < content.length) {
    const lineEnd = content.indexOf("\n", lineStart);
    const end = lineEnd < 0 ? content.length : lineEnd;
    const line = content.slice(lineStart, end);
    const candidate = line.match(/^[ \t]*(?:>[ \t]*)*([`~]+)[ \t]*\r?$/)?.[1];
    if (
      candidate
      && candidate[0] === markerCharacter
      && candidate.length >= marker.length
    ) {
      return lineEnd < 0 ? content.length : lineEnd + 1;
    }
    if (lineEnd < 0) return content.length;
    lineStart = lineEnd + 1;
  }
  return content.length;
}

function inlineCodeEnd(content: string, opening: number, markerLength: number): number {
  const lineEnd = content.indexOf("\n", opening + markerLength);
  const limit = lineEnd < 0 ? content.length : lineEnd;
  let cursor = opening + markerLength;
  while (cursor < limit) {
    if (content[cursor] !== "`") {
      cursor += 1;
      continue;
    }
    let closingLength = 1;
    while (content[cursor + closingLength] === "`") closingLength += 1;
    if (closingLength === markerLength) return cursor + closingLength;
    cursor += closingLength;
  }
  return limit;
}

function normalizeOutsideMarkdownCode(content: string): string {
  const output: string[] = [];
  let plainStart = 0;
  let cursor = 0;
  while (cursor < content.length) {
    const atLineStart = cursor === 0 || content[cursor - 1] === "\n";
    if (atLineStart) {
      const lineEnd = content.indexOf("\n", cursor);
      const openingLine = content.slice(cursor, lineEnd < 0 ? content.length : lineEnd);
      const opening = openingLine.match(FENCE_OPENING);
      if (opening) {
        output.push(normalizeLatexMathSegment(content.slice(plainStart, cursor)));
        const end = fenceEnd(content, cursor + opening[0].length, opening[2]);
        output.push(content.slice(cursor, end));
        cursor = end;
        plainStart = end;
        continue;
      }
    }
    if (content[cursor] === "`") {
      let markerLength = 1;
      while (content[cursor + markerLength] === "`") markerLength += 1;
      output.push(normalizeLatexMathSegment(content.slice(plainStart, cursor)));
      const end = inlineCodeEnd(content, cursor, markerLength);
      output.push(content.slice(cursor, end));
      cursor = end;
      plainStart = end;
      continue;
    }
    cursor += 1;
  }
  output.push(normalizeLatexMathSegment(content.slice(plainStart)));
  return output.join("");
}

/**
 * Convert LaTeX's explicit math delimiters into the dollar syntax understood
 * by remark-math. Conversion is paired, code-safe, and keeps the independent
 * Markdown container prefix of both display delimiters. In particular, the
 * closing marker must retain list indentation or blockquote markers.
 */
export function normalizeLatexMathMarkdown(content: string): string {
  return normalizeOutsideMarkdownCode(content);
}

export function prepareMarkdown(content: string): string {
  return normalizeLatexMathMarkdown(normalizeStrongMarkdown(content))
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
