import type { TFunction } from "i18next";

import { ApiError } from "@/lib/api-error";

const ERROR_CODE_KEYS: Record<string, string> = {
  agent_unavailable: "chat:errors.agent_unavailable",
  chat_message_empty: "chat:errors.chat_message_empty",
  context_too_large: "chat:errors.context_too_large",
  context_not_found: "chat:errors.context_not_found",
  context_not_ready: "chat:errors.context_not_ready",
  task_not_found: "chat:errors.task_not_found",
  task_running: "chat:errors.task_running",
  subtask_not_found: "chat:errors.subtask_not_found",
  model_override_not_allowed: "chat:errors.model_override_not_allowed",
  model_unavailable: "chat:errors.model_unavailable",
  provider_call_failed: "chat:errors.provider_call_failed",
  provider_invalid_response: "chat:errors.provider_invalid_response",
  upload_filename_invalid: "chat:errors.upload_filename_invalid",
  upload_type_invalid: "chat:errors.upload_type_invalid",
  upload_too_large: "chat:errors.upload_too_large",
  upload_empty: "chat:errors.upload_empty",
  extraction_invalid: "chat:errors.extraction_invalid",
  extraction_empty: "chat:errors.extraction_empty",
  extraction_too_large: "chat:errors.extraction_too_large",
  extraction_unsupported: "chat:errors.extraction_unsupported",
};

export function getErrorMessage(
  error: unknown,
  t: TFunction,
  fallbackKey: string,
): string {
  if (error instanceof ApiError) {
    if (error.code) {
      const key = ERROR_CODE_KEYS[error.code];
      if (key) {
        return t(key);
      }
    }
    return t(fallbackKey);
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return t(fallbackKey);
}
