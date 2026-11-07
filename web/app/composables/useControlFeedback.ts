export function controlResponseStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object") return undefined;
  if ("statusCode" in error && typeof error.statusCode === "number") return error.statusCode;
  if (!("response" in error)) return undefined;
  const response = error.response;
  if (!response || typeof response !== "object" || !("status" in response)) return undefined;
  return typeof response.status === "number" ? response.status : undefined;
}

export function controlResponseCode(error: unknown): string | undefined {
  if (!error || typeof error !== "object") return undefined;
  if ("data" in error) {
    const code = errorCodeFromEnvelope(error.data);
    if (code) return code;
  }
  if (!("response" in error)) return undefined;
  const response = error.response;
  if (!response || typeof response !== "object" || !("_data" in response)) return undefined;
  return errorCodeFromEnvelope(response._data);
}

export function controlErrorMessage(
  error: unknown,
  fallback = "The operation could not be completed.",
): string {
  switch (controlResponseStatus(error)) {
    case 401:
      return "Your Telegram session expired. Sign in again.";
    case 403:
      if (controlResponseCode(error) === "csrf_rejected") {
        return "Your session security token is out of date. Refresh this page and retry.";
      }
      return "Your Telegram account does not have access to this scope.";
    case 409:
      return "This record changed in another session. Refresh before retrying.";
    case 422:
      return "Check the highlighted values and try again.";
    case 429:
      return "Too many requests. Wait a moment before retrying.";
    default:
      return fallback;
  }
}

export function isAbortError(error: unknown): boolean {
  return Boolean(
    error && typeof error === "object" && "name" in error && error.name === "AbortError",
  );
}

function errorCodeFromEnvelope(value: unknown): string | undefined {
  if (!value || typeof value !== "object" || !("error" in value)) return undefined;
  const envelope = value.error;
  if (!envelope || typeof envelope !== "object" || !("code" in envelope)) return undefined;
  return typeof envelope.code === "string" ? envelope.code : undefined;
}
