export const INTERVIEW_SESSIONS_CHANGED_EVENT = "auto-reign:interview-sessions-changed";

export function notifyInterviewSessionsChanged() {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new Event(INTERVIEW_SESSIONS_CHANGED_EVENT));
}
