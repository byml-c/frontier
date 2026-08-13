import assert from "node:assert/strict";
import test from "node:test";

import {
  activitiesFromContext,
  activityPhasesForDisplay,
  groupActivities,
  topLevelActivityStatus,
  type AgentActivityItem,
  updateActivities,
} from "../src/components/agentActivityState.ts";

const activity = (value: Partial<AgentActivityItem> & Pick<AgentActivityItem, "id" | "kind">): AgentActivityItem => ({
  label: value.kind === "reasoning" ? "正在分析问题" : "正在调用工具",
  status: "completed",
  ...value,
});

test("reasoning events split tool calls into sequential analysis phases", () => {
  const phases = groupActivities([
    activity({ id: "startup", kind: "status", label: "Agent 已连接" }),
    activity({ id: "reason-1", kind: "reasoning", detail: "先检查论文资料" }),
    activity({ id: "tool-1", kind: "tool", label: "已读取论文" }),
    activity({ id: "tool-2", kind: "tool", label: "已读取研究图谱" }),
    activity({ id: "reason-2", kind: "reasoning", detail: "比较证据与已有方向", status: "running" }),
    activity({ id: "tool-3", kind: "tool", status: "running" }),
  ]);

  assert.deepEqual(phases.map((phase) => ({
    id: phase.id,
    kind: phase.kind,
    detail: phase.detail,
    status: phase.status,
    toolCount: phase.toolCount,
    itemIds: phase.items.map((item) => item.id),
  })), [
    { id: "preparation-startup", kind: "preparation", detail: undefined, status: "completed", toolCount: 0, itemIds: ["startup"] },
    { id: "analysis-reason-1", kind: "analysis", detail: "先检查论文资料", status: "completed", toolCount: 2, itemIds: ["reason-1", "tool-1", "tool-2"] },
    { id: "analysis-reason-2", kind: "analysis", detail: "比较证据与已有方向", status: "running", toolCount: 1, itemIds: ["reason-2", "tool-3"] },
  ]);
});

test("a failed tool marks its analysis phase as failed", () => {
  const [phase] = groupActivities([
    activity({ id: "reason", kind: "reasoning" }),
    activity({ id: "tool", kind: "tool", status: "failed" }),
  ]);

  assert.equal(phase.status, "failed");
  assert.equal(phase.toolCount, 1);
});

test("live and persisted activity histories are not truncated to eight items", () => {
  const events = Array.from({ length: 12 }, (_, index) => ({
    type: "activity",
    activity_id: `tool-${index}`,
    kind: "tool",
    label: `工具 ${index}`,
    status: "completed",
  }));
  const live = events.reduce<AgentActivityItem[]>(
    (items, event) => updateActivities(items, event),
    [],
  );
  const persisted = activitiesFromContext({
    activities: live.map((item) => ({ ...item })),
  });

  assert.equal(live.length, 12);
  assert.equal(persisted.length, 12);
});

test("completed activity hides only the trailing reasoning-only output phase", () => {
  const phases = groupActivities([
    activity({ id: "reason-1", kind: "reasoning" }),
    activity({ id: "tool-1", kind: "tool" }),
    activity({ id: "reason-2", kind: "reasoning" }),
  ]);

  assert.deepEqual(
    activityPhasesForDisplay(phases, false).map((phase) => phase.id),
    ["analysis-reason-1"],
  );
  assert.equal(activityPhasesForDisplay(phases, true).length, 2);

  const toolBackedLastPhase = groupActivities([
    activity({ id: "reason-1", kind: "reasoning" }),
    activity({ id: "reason-2", kind: "reasoning" }),
    activity({ id: "tool-2", kind: "tool" }),
  ]);
  assert.equal(activityPhasesForDisplay(toolBackedLastPhase, false).length, 2);
});

test("connection lifecycle is shown at the top instead of a preparation card", () => {
  const items = [
    activity({ id: "agent-startup", kind: "status", label: "Agent 已连接" }),
    activity({ id: "reason-1", kind: "reasoning", status: "running" }),
  ];
  const phases = activityPhasesForDisplay(groupActivities(items), true);

  assert.equal(topLevelActivityStatus(items)?.label, "Agent 已连接");
  assert.deepEqual(phases.map((phase) => phase.id), ["analysis-reason-1"]);
});
