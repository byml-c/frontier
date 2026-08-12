import type { KeyboardEvent } from "react";

type ChatKey = {
  key: string;
  shiftKey: boolean;
  keyCode: number;
  isComposing: boolean;
};

export function shouldSubmitChatOnEnter(event: ChatKey): boolean {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229;
}

/** Submit a chat form with Enter while preserving multiline and IME input. */
export function submitChatOnEnter(event: KeyboardEvent<HTMLElement>) {
  if (!shouldSubmitChatOnEnter({
    key: event.key,
    shiftKey: event.shiftKey,
    keyCode: event.keyCode,
    isComposing: event.nativeEvent.isComposing,
  })) return;

  event.preventDefault();
  const input = event.target as HTMLInputElement | HTMLTextAreaElement;
  input.form?.requestSubmit();
}
