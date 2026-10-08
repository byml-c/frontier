import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const component = readFileSync(new URL("../src/components/BriefPodcast.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../src/pages/BriefPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");

test("brief page keeps voice playback disabled", () => {
  assert.doesNotMatch(page, /<BriefPodcast briefId=\{data\.id\}/);
  assert.match(component, /单人播报/);
  assert.match(component, /component="audio"/);
  assert.match(component, /controls/);
  assert.doesNotMatch(component, /autoPlay/);
});

test("podcast exposes accessible transcript and responsive controls", () => {
  assert.match(component, /aria-expanded=\{expanded\}/);
  assert.match(component, /aria-controls=\{transcriptId\}/);
  assert.match(component, /<Collapse in=\{expanded\}/);
  assert.match(component, /alignSelf: \{ xs: "stretch", sm: "center" \}/);
});

test("podcast handles missing, generating, failed and ready states", () => {
  assert.match(component, /episode\.status === "missing"/);
  assert.match(component, /GENERATING_STATUSES/);
  assert.match(component, /episode\?\.status === "failed"/);
  assert.match(component, /episode\?\.status === "ready"/);
  assert.match(component, /重新生成并替换音频/);
  assert.match(component, /refetchInterval/);
});

test("podcast API treats a missing episode as an empty state", () => {
  assert.match(api, /getBriefPodcast/);
  assert.match(api, /error\.status === 404/);
  assert.match(api, /\/podcast\/generate/);
});
