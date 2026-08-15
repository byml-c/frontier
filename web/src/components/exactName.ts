export const normalizeExactName = (value: string) => value.trim().toLocaleLowerCase();

export function canCreateExactName(
  input: string,
  existingNames: string[],
  pendingNames: string[],
): boolean {
  const normalizedInput = normalizeExactName(input);
  return Boolean(normalizedInput)
    && !existingNames.some((name) => normalizeExactName(name) === normalizedInput)
    && !pendingNames.some((name) => normalizeExactName(name) === normalizedInput);
}
