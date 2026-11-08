export function authorsText(authors: readonly { name: string }[]): string {
  return authors.map((author) => author.name.trim()).filter(Boolean).join(", ");
}

export function parseAuthors<T extends { name: string }>(text: string, current: readonly T[] = []): Array<T | { name: string }> {
  const remaining = [...current];
  const nameKey = (name: string) => name.trim().replace(/\s+/g, " ").toLowerCase();
  return text.split(/[,，\n]+/).map((name) => name.trim()).filter(Boolean).map((name) => {
    const index = remaining.findIndex((author) => nameKey(author.name) === nameKey(name));
    return index < 0 ? { name } : { ...remaining.splice(index, 1)[0], name };
  });
}
