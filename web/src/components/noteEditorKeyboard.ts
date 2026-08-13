export type NoteEditorKeyboardInput = {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  isComposing: boolean;
};

export function noteEditorKeyboardAction(event: NoteEditorKeyboardInput): "save" | "cancel" | null {
  if (event.isComposing) return null;
  if (event.key === "Escape") return "cancel";
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) return "save";
  return null;
}
