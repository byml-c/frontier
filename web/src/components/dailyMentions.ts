const CODE_REGION = /(```[\s\S]*?```|`[^`\n]*`)/g;

export function linkifyDailyMentions(content: string, names: string[]): string {
  const known = new Set(names);
  return content.split(CODE_REGION).map((segment, index) => {
    if (index % 2 === 1) return segment;
    return segment.replace(
      /(^|[^A-Za-z0-9@])@([A-Za-z0-9]{4})(?![A-Za-z0-9])/g,
      (match, prefix: string, name: string) => known.has(name)
        ? `${prefix}[@${name}](#frontier-mention-${name})`
        : match,
    );
  }).join("");
}

export function mentionDraft(content: string, name: string): string {
  const mention = `@${name}`;
  if (content.includes(mention)) return content;
  return content.trim() ? `${content.trimEnd()} ${mention} ` : `${mention} `;
}
