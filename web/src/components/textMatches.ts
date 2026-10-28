export type TextMatch = { text: string; matched: boolean };

export function queryTerms(...values: string[]): string[] {
  return [...new Set(values.flatMap((value) => value.trim().split(/\s+/u)).filter(Boolean))].slice(0, 100);
}

export function splitTextMatches(text: string, terms: readonly string[]): TextMatch[] {
  const escaped = [...new Set(terms.filter(Boolean))].sort((a, b) => b.length - a.length).map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!escaped.length) return [{ text, matched: false }];
  const pattern = new RegExp(escaped.join("|"), "giu");
  const parts: TextMatch[] = [];
  let position = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index;
    if (index > position) parts.push({ text: text.slice(position, index), matched: false });
    parts.push({ text: match[0], matched: true });
    position = index + match[0].length;
  }
  if (position < text.length || !parts.length) parts.push({ text: text.slice(position), matched: false });
  return parts;
}

export function matchExcerpt(text: string, terms: readonly string[], context = 70): string | null {
  const parts = splitTextMatches(text, terms);
  const index = parts.findIndex((part) => part.matched);
  if (index < 0) return null;
  const offset = parts.slice(0, index).reduce((size, part) => size + part.text.length, 0);
  const start = Math.max(0, offset - context);
  const end = Math.min(text.length, offset + parts[index].text.length + context);
  return `${start ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
}
