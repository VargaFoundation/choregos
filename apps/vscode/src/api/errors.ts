// SPDX-License-Identifier: Apache-2.0
/** An answer of the platform that is not a success, read from its RFC 9457 problem document when it has one. */
export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  /** `errors[0].error` when the problem names one (`step_up_required`, `decision_requires_session`…). */
  readonly code: string | undefined;

  constructor(status: number, title: string, detail = "", code?: string) {
    super(detail || title);
    this.name = "ApiError";
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.code = code;
  }
}

/** The client refused to send the request, or to read its answer, before the platform said anything. */
export class ClientRefusal extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClientRefusal";
  }
}

interface Problem {
  title?: unknown;
  detail?: unknown;
  errors?: unknown;
}

function codeOf(errors: unknown): string | undefined {
  if (!Array.isArray(errors)) return undefined;
  const first: unknown = errors[0];
  if (first && typeof first === "object" && "error" in first && typeof first.error === "string") {
    return first.error;
  }
  return undefined;
}

/** The error an answer carries: the problem's own words, or the status when the body says nothing readable. */
export async function errorOf(response: Response): Promise<ApiError> {
  const fallback = response.statusText || `HTTP ${response.status}`;
  let text = "";
  try {
    text = await response.text();
  } catch {
    return new ApiError(response.status, fallback);
  }
  try {
    const problem = JSON.parse(text) as Problem;
    const title = typeof problem.title === "string" ? problem.title : fallback;
    const detail = typeof problem.detail === "string" ? problem.detail : "";
    return new ApiError(response.status, title, detail, codeOf(problem.errors));
  } catch {
    return new ApiError(response.status, fallback);
  }
}
