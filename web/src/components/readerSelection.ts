export type CaretPoint = { node: Node; offset: number };

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
