export type CaretPoint = { node: Node; offset: number };

export type TextRangeSelection = {
  text: string;
  page: number;
  rect: DOMRect;
  segments: Array<{ blockId: string; startOffset: number; endOffset: number }>;
  pageContext: string;
};

export function caretAtPoint(x: number, y: number): CaretPoint | null {
  const modern = document.caretPositionFromPoint?.(x, y);
  if (modern) return { node: modern.offsetNode, offset: modern.offset };
  const legacy = (document as Document & { caretRangeFromPoint?: (x: number, y: number) => Range | null }).caretRangeFromPoint?.(x, y);
  return legacy ? { node: legacy.startContainer, offset: legacy.startOffset } : null;
}

export function offsetWithin(root: HTMLElement, node: Node, offset: number) {
  if (!root.contains(node)) return null;
  const prefix = document.createRange();
  prefix.selectNodeContents(root);
  try { prefix.setEnd(node, offset); } catch { return null; }
  return prefix.toString().length;
}

export function rangeFromOffsets(root: HTMLElement, start: number, end: number) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let cursor = 0;
  let startPoint: { node: Text; offset: number } | null = null;
  let endPoint: { node: Text; offset: number } | null = null;
  let current: Node | null;
  while ((current = walker.nextNode())) {
    const node = current as Text;
    const next = cursor + (node.nodeValue?.length || 0);
    if (!startPoint && start <= next) startPoint = { node, offset: Math.max(0, start - cursor) };
    if (!endPoint && end <= next) {
      endPoint = { node, offset: Math.max(0, end - cursor) };
      break;
    }
    cursor = next;
  }
  if (!startPoint || !endPoint) return null;
  const range = document.createRange();
  range.setStart(startPoint.node, startPoint.offset);
  range.setEnd(endPoint.node, endPoint.offset);
  return range;
}

export function textSelectionFromRange(root: HTMLElement, range: Range): TextRangeSelection | null {
  const text = range.toString().trim();
  if (!text) return null;
  const candidates = [...root.querySelectorAll<HTMLElement>("[data-text-block]")];
  const rawSegments = candidates.flatMap((block) => {
    try {
      if (!range.intersectsNode(block)) return [];
    } catch {
      return [];
    }
    const fullText = block.textContent || "";
    const startOffset = block.contains(range.startContainer)
      ? offsetWithin(block, range.startContainer, range.startOffset) ?? 0
      : 0;
    const endOffset = block.contains(range.endContainer)
      ? offsetWithin(block, range.endContainer, range.endOffset) ?? fullText.length
      : fullText.length;
    if (endOffset <= startOffset) return [];
    return [{ block, blockId: block.dataset.blockId || "", startOffset, endOffset }];
  });
  if (!rawSegments.length) return null;
  const firstRaw = rawSegments[0];
  const lastRaw = rawSegments.at(-1)!;
  const firstText = (firstRaw.block.textContent || "").slice(firstRaw.startOffset, firstRaw.endOffset);
  const lastText = (lastRaw.block.textContent || "").slice(lastRaw.startOffset, lastRaw.endOffset);
  firstRaw.startOffset += firstText.length - firstText.trimStart().length;
  lastRaw.endOffset -= lastText.length - lastText.trimEnd().length;
  const selected = rawSegments.filter((item) => item.blockId && item.endOffset > item.startOffset);
  if (!selected.length) return null;
  const rect = range.getBoundingClientRect();
  return {
    text,
    page: Number(selected[0].block.dataset.pageNumber || 1),
    rect,
    segments: selected.map(({ blockId, startOffset, endOffset }) => ({ blockId, startOffset, endOffset })),
    pageContext: selected.map(({ block, startOffset, endOffset }) => {
      const fullText = block.textContent || "";
      return fullText.slice(Math.max(0, startOffset - 180), endOffset + 180);
    }).join("\n\n"),
  };
}

export function wordBounds(text: string, offset: number) {
  const clamped = Math.max(0, Math.min(offset, text.length));
  const segments = typeof Intl.Segmenter === "function"
    ? [...new Intl.Segmenter(undefined, { granularity: "word" }).segment(text)]
    : [];
  const segment = segments.find((item) => item.index <= clamped && item.index + item.segment.length >= clamped && item.segment.trim());
  if (segment) return { start: segment.index, end: segment.index + segment.segment.length };
  let start = clamped; let end = clamped;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  return end > start ? { start, end } : { start: Math.max(0, clamped - 1), end: Math.min(text.length, clamped + 1) };
}
