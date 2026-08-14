import type { ArchitectureNode, ArchitectureSize } from "./types";

const MIN_CARD_WIDTH = 184;
const MAX_CARD_WIDTH = 320;

function textWidth(value: string, fontSize: number): number {
  return Array.from(value).reduce((width, character) => {
    if (/\s/u.test(character)) return width + fontSize * .3;
    if (/\p{Script=Han}|\p{Script=Hiragana}|\p{Script=Katakana}/u.test(character)) return width + fontSize;
    if (/[A-Z0-9]/u.test(character)) return width + fontSize * .62;
    return width + fontSize * .52;
  }, 0);
}

function wrappedLines(width: number, available: number): number {
  return Math.max(1, Math.ceil(width / Math.max(1, available)));
}

export function architectureCardSize(node: ArchitectureNode): ArchitectureSize {
  const details = node.type === "module" ? node.keywords ?? [] : node.preprocessing ?? [];
  const hasImageExample = node.type === "data" && Boolean(node.examples?.some((example) => example.type === "image"));
  const titleWidth = textWidth(node.title, 15) + (node.symbol ? textWidth(node.symbol, 16) + 12 : 0);
  const architectureWidth = node.type === "module" && node.architecture ? textWidth(node.architecture, 13) + 20 : 0;
  const detailsWidth = details.length ? textWidth(details.join(node.type === "module" ? " · " : " → "), 12) : 0;
  const imageWidth = hasImageExample ? 240 : 0;
  const width = Math.round(Math.min(MAX_CARD_WIDTH, Math.max(MIN_CARD_WIDTH, titleWidth + 28, architectureWidth + 28, detailsWidth + 28, imageWidth)));
  const available = width - 28;
  const titleLines = wrappedLines(titleWidth, available);
  const architectureLines = architectureWidth ? wrappedLines(architectureWidth - 20, available - 14) : 0;
  const detailLines = detailsWidth ? wrappedLines(detailsWidth, available) : 0;
  const imageHeight = hasImageExample ? 72 : 0;
  const sectionGaps = [architectureLines, detailLines, imageHeight].filter(Boolean).length * 4;
  const height = Math.round(22 + titleLines * 19 + architectureLines * 18 + detailLines * 16 + imageHeight + sectionGaps);
  return { width, height };
}
