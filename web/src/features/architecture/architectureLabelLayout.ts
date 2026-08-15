export type ArchitectureRect = { x: number; y: number; width: number; height: number };
export type ArchitectureLabelCandidate = ArchitectureRect & { id: string; preferredX: number; preferredY: number };

const LABEL_MARGIN = 8;
const MAX_DISPLACEMENT = 180;

function overlap(first: ArchitectureRect, second: ArchitectureRect) {
  const horizontal = Math.min(first.x + first.width, second.x + second.width) - Math.max(first.x, second.x);
  const vertical = Math.min(first.y + first.height, second.y + second.height) - Math.max(first.y, second.y);
  return { horizontal, vertical };
}

function centered(candidate: ArchitectureLabelCandidate): ArchitectureRect {
  return { x: candidate.x - candidate.width / 2, y: candidate.y - candidate.height / 2, width: candidate.width, height: candidate.height };
}

/** Resolve visible edge labels with a spring to their edge and repulsion from nodes and peers. */
export function placeArchitectureEdgeLabels(candidates: ArchitectureLabelCandidate[], obstacles: ArchitectureRect[], iterations = 14): Record<string, { x: number; y: number }> {
  const labels = candidates.map((candidate) => ({ ...candidate, x: candidate.preferredX, y: candidate.preferredY }));
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    labels.forEach((label) => { label.x += (label.preferredX - label.x) * .08; label.y += (label.preferredY - label.y) * .08; });
    labels.forEach((label) => obstacles.forEach((obstacle) => {
      const padded = { x: obstacle.x - LABEL_MARGIN, y: obstacle.y - LABEL_MARGIN, width: obstacle.width + LABEL_MARGIN * 2, height: obstacle.height + LABEL_MARGIN * 2 };
      const collision = overlap(centered(label), padded);
      if (collision.horizontal <= 0 || collision.vertical <= 0) return;
      const obstacleCenterX = obstacle.x + obstacle.width / 2; const obstacleCenterY = obstacle.y + obstacle.height / 2;
      if (collision.horizontal < collision.vertical) label.x += (label.x >= obstacleCenterX ? 1 : -1) * (collision.horizontal + 2);
      else label.y += (label.y >= obstacleCenterY ? 1 : -1) * (collision.vertical + 2);
    }));
    labels.forEach((first, index) => labels.slice(index + 1).forEach((second) => {
      const collision = overlap(centered(first), centered(second));
      if (collision.horizontal <= -LABEL_MARGIN || collision.vertical <= -LABEL_MARGIN) return;
      const pushX = Math.max(0, collision.horizontal + LABEL_MARGIN); const pushY = Math.max(0, collision.vertical + LABEL_MARGIN);
      if (pushX < pushY) {
        const direction = first.x === second.x ? (first.id < second.id ? -1 : 1) : Math.sign(first.x - second.x);
        first.x += direction * pushX / 2; second.x -= direction * pushX / 2;
      } else {
        const direction = first.y === second.y ? (first.id < second.id ? -1 : 1) : Math.sign(first.y - second.y);
        first.y += direction * pushY / 2; second.y -= direction * pushY / 2;
      }
    }));
    labels.forEach((label) => {
      label.x = Math.max(label.preferredX - MAX_DISPLACEMENT, Math.min(label.preferredX + MAX_DISPLACEMENT, label.x));
      label.y = Math.max(label.preferredY - MAX_DISPLACEMENT, Math.min(label.preferredY + MAX_DISPLACEMENT, label.y));
    });
  }
  return Object.fromEntries(labels.map((label) => [label.id, { x: Math.round(label.x), y: Math.round(label.y) }]));
}
