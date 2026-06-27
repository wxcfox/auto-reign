export const CONVERSATIONS_CHANGED_EVENT = "auto-reign:conversations-changed";

export function notifyConversationsChanged() {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new Event(CONVERSATIONS_CHANGED_EVENT));
}
