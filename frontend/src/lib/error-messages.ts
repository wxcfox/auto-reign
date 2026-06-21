import type { TFunction } from "i18next";

import { ApiError } from "@/lib/api-error";

const ERROR_CODE_KEYS: Record<string, string> = {
  answer_already_submitted: "errors.answer_already_submitted",
  current_turn_unanswered: "errors.current_turn_unanswered",
  document_not_found: "errors.document_not_found",
  embedding_call_failed: "errors.embedding_call_failed",
  follow_up_already_submitted: "errors.follow_up_already_submitted",
  main_answer_required: "errors.main_answer_required",
  model_not_configured: "errors.model_not_configured",
  provider_call_failed: "errors.provider_call_failed",
  provider_invalid_response: "errors.provider_invalid_response",
  provider_not_configured: "errors.provider_not_configured",
  session_not_active: "errors.session_not_active",
  session_not_found: "errors.session_not_found",
  target_rounds_reached: "errors.target_rounds_reached",
  vector_dimension_mismatch: "errors.vector_dimension_mismatch",
  vector_store_unavailable: "errors.vector_store_unavailable",
};

export function getErrorMessage(
  error: unknown,
  t: TFunction,
  fallbackKey: string,
): string {
  if (error instanceof ApiError && error.code) {
    const key = ERROR_CODE_KEYS[error.code];
    if (key) {
      return t(key);
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return t(fallbackKey);
}
