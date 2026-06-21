export class ApiError extends Error {
  code?: string;
  status: number;

  constructor(message: string, options: { code?: string; status: number }) {
    super(message);
    this.name = "ApiError";
    this.code = options.code;
    this.status = options.status;
  }
}

type ApiErrorDetail = {
  code?: string;
  message?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export async function throwApiError(response: Response, fallbackMessage: string): Promise<never> {
  const contentType = response.headers.get("content-type") ?? "";

  if (contentType.includes("application/json")) {
    const body: unknown = await response.json().catch(() => null);
    const detail = isRecord(body) && isRecord(body.detail) ? (body.detail as ApiErrorDetail) : null;
    throw new ApiError(detail?.message ?? fallbackMessage, {
      code: detail?.code,
      status: response.status,
    });
  }

  const text = await response.text().catch(() => "");
  throw new ApiError(text || fallbackMessage, { status: response.status });
}
