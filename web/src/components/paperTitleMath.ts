export type PaperTitleSegment =
  | { kind: "text"; value: string }
  | { kind: "math"; source: string; raw: string };

const delimiters = [
  { opening: "$$", closing: "$$" },
  { opening: "\\[", closing: "\\]" },
  { opening: "\\(", closing: "\\)" },
  { opening: "$", closing: "$" },
] as const;

function isEscaped(value: string, index: number): boolean {
  let backslashes = 0;
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) {
    backslashes += 1;
  }
  return backslashes % 2 === 1;
}

function closingIndex(value: string, closing: string, start: number): number {
  let cursor = start;
  while (cursor < value.length) {
    const candidate = value.indexOf(closing, cursor);
    if (candidate < 0) return -1;
    if (!isEscaped(value, candidate)) return candidate;
    cursor = candidate + closing.length;
  }
  return -1;
}

/** Parse only explicitly delimited title math; all other title text stays literal. */
export function parsePaperTitleMath(value: string): PaperTitleSegment[] {
  const segments: PaperTitleSegment[] = [];
  let textStart = 0;
  let cursor = 0;

  while (cursor < value.length) {
    const delimiter = delimiters.find(
      ({ opening }) => value.startsWith(opening, cursor) && !isEscaped(value, cursor),
    );
    if (!delimiter) {
      cursor += 1;
      continue;
    }
    const bodyStart = cursor + delimiter.opening.length;
    const end = closingIndex(value, delimiter.closing, bodyStart);
    if (end < 0) {
      cursor += delimiter.opening.length;
      continue;
    }
    const source = value.slice(bodyStart, end).trim();
    if (!source) {
      cursor = end + delimiter.closing.length;
      continue;
    }
    if (cursor > textStart) segments.push({ kind: "text", value: value.slice(textStart, cursor) });
    const rawEnd = end + delimiter.closing.length;
    segments.push({ kind: "math", source, raw: value.slice(cursor, rawEnd) });
    cursor = rawEnd;
    textStart = rawEnd;
  }

  if (textStart < value.length) segments.push({ kind: "text", value: value.slice(textStart) });
  return segments.length ? segments : [{ kind: "text", value }];
}
