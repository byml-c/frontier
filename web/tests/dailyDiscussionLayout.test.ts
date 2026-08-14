import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/components/DailyDiscussion.tsx", import.meta.url), "utf8");

test("desktop daily discussion surface matches the centered paper card width", () => {
  assert.match(source, /theme\.breakpoints\.values\.md/);
  assert.match(source, /left: 0, right: 0, mx: "auto"/);
  assert.doesNotMatch(source, /md: "translateX\(-50%\)"/);
});

test("discussion action floats outside the drawer stacking context", () => {
  assert.match(source, /<Portal>/);
  assert.match(source, /theme\.zIndex\.modal \+ 1/);
});

test("discussion starts collapsed and keeps its count above the floating action", () => {
  assert.match(source, /const \[open, setOpen\] = useState\(false\)/);
  assert.doesNotMatch(source, /enabled: open/);
  assert.match(source, /\.MuiBadge-badge/);
  assert.match(source, /top: -3, right: -5, transform: "none"/);
  assert.match(source, /打开论文评论[^\n]+sx=\{\{ zIndex: 0 \}\}/);
});
