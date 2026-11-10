export function paperReadingHref(paperId: string, briefItemId?: string): string {
  const query = new URLSearchParams();
  if (briefItemId) query.set("source_brief_item_id", briefItemId);
  return `/paper/${encodeURIComponent(paperId)}${query.size ? `?${query}` : ""}`;
}

export function readingSourceBriefItem(search: string): string | undefined {
  return new URLSearchParams(search).get("source_brief_item_id") || undefined;
}
