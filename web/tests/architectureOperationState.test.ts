import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  architectureOperationIsActive,
  type ArchitectureDocument,
} from "../src/features/architecture/types.ts";

function document(overrides: Partial<ArchitectureDocument>): ArchitectureDocument {
  return {
    paperId: "paper-1",
    status: "ready",
    revision: 1,
    schemaVersion: 1,
    ...overrides,
  };
}

test("architecture operation stays active through the commit-to-runner window", () => {
  assert.equal(architectureOperationIsActive(document({ status: "generating" })), true);
  assert.equal(architectureOperationIsActive(document({ taskId: "task-1" })), true);
  assert.equal(
    architectureOperationIsActive(
      document({ lock: { ownerType: "agent", ownerId: "task-1" } }),
    ),
    true,
  );
  assert.equal(architectureOperationIsActive(document({})), false);
});

test("reader keeps observing architecture generation outside architecture mode", () => {
  const source = readFileSync(
    new URL("../src/pages/ReaderPage.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /const architectureStatus = useQuery/);
  assert.match(source, /architectureOperationIsActive\(architectureStatus\.data\)/);
  assert.match(source, /Agent 正在后台生成架构，切换阅读模式不会中断任务。/);
  assert.match(
    source,
    /externalBusy=\{architectureGenerationActive \|\| \(architectureMode && architectureAgentBusy\)\}/,
  );
});
