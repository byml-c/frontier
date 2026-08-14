function normalizeTextScripts(value: string): string {
  return value
    .replace(/([_^])\{([A-Za-z][A-Za-z0-9]{1,})\}/g, (_, operator: string, token: string) => `${operator}{\\rm ${token}}`)
    .replace(/([_^])(?!\{|\\)([A-Za-z][A-Za-z0-9]{1,}|[0-9]{2,})/g, (_, operator: string, token: string) => (
      /^[0-9]+$/.test(token) ? `${operator}{${token}}` : `${operator}{\\rm ${token}}`
    ));
}

export function normalizeArchitectureMathSource(value: string): string {
  const source = value.trim();
  const pairs: Array<[string, string]> = [["$$", "$$"], ["$", "$"], ["\\(", "\\)"], ["\\[", "\\]"]];
  for (const [opening, closing] of pairs) {
    if (source.startsWith(opening) && source.endsWith(closing) && source.length > opening.length + closing.length) {
      return normalizeTextScripts(source.slice(opening.length, -closing.length).trim());
    }
  }
  return normalizeTextScripts(source);
}

const INLINE_MATH_CANDIDATE = /(?<![\p{L}\p{N}\\])(?:\\?[\p{L}][\p{L}\p{N}*]*)(?:[_^](?:\{[^}\s]+\}|[\p{L}\p{N}*]+))+(?:=[^\s·,，;；]+)?/gu;
const PROTECTED_MATH = /(\$\$[\s\S]*?\$\$|\$(?!\$)[^$\n]*\$(?!\$)|\\\[[\s\S]*?\\\]|\\\([^\n]*?\\\))/g;

/**
 * Older scenes sometimes stored TeX-like identifiers as ordinary prose. Wrap
 * only tokens with an explicit sub/superscript marker, while leaving authored
 * Markdown math untouched. This is a display compatibility layer; new scenes
 * store exact identifiers in `symbol`.
 */
export function normalizeArchitectureInlineMath(content: string): string {
  return content.split(PROTECTED_MATH).map((segment, index) => (
    index % 2 === 1
      ? normalizeProtectedMath(segment)
      : segment.replace(INLINE_MATH_CANDIDATE, (value) => `$${normalizeArchitectureMathSource(value)}$`)
  )).join("");
}

function normalizeProtectedMath(value: string): string {
  const pairs: Array<[string, string]> = [["$$", "$$"], ["$", "$"], ["\\(", "\\)"], ["\\[", "\\]"]];
  const pair = pairs.find(([opening, closing]) => value.startsWith(opening) && value.endsWith(closing));
  if (!pair) return value;
  const [opening, closing] = pair;
  return `${opening}${normalizeArchitectureMathSource(value.slice(opening.length, -closing.length))}${closing}`;
}

export function parseArchitectureList(value: string, type: "module" | "data"): string[] {
  return value.split(type === "module" ? /·|\n/ : /→|\n/).map((item) => item.trim()).filter(Boolean);
}
