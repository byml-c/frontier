import type { CodexModelCatalog, CodexSelection } from "../types.ts";

export function normalizeCodexSelection(catalog?: CodexModelCatalog, requested?: CodexSelection | null): CodexSelection | undefined {
  const available = catalog?.backends.filter((backend) => backend.available && backend.models.length);
  const backend = available?.find((item) => item.name === requested?.backend)
    ?? available?.find((item) => item.name === catalog?.default_backend)
    ?? available?.[0];
  if (!backend) return undefined;
  const model = backend.models.find((item) => backend.name === requested?.backend && item.id === requested.model)
    ?? backend.models.find((item) => item.id === backend.default_model)
    ?? backend.models[0];
  const effort = requested?.reasoning_effort;
  return {
    backend: backend.name,
    model: model.id,
    reasoning_effort: effort && model.reasoning_efforts.includes(effort)
      ? effort : model.default_reasoning_effort,
  };
}
