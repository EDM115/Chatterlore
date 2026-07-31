type ControlApiMethod = "GET" | "POST" | "PATCH" | "DELETE";

interface ControlApiOptions {
  method?: ControlApiMethod;
  body?: unknown;
}

export function useControlApi() {
  const config = useRuntimeConfig();
  const csrfToken = useState<string | null>("control-plane-csrf", () => null);

  async function request<T>(path: string, options: ControlApiOptions = {}): Promise<T> {
    const method = options.method ?? "GET";
    const headers: Record<string, string> = {};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (!["GET"].includes(method) && csrfToken.value) {
      headers["X-CSRF-Token"] = csrfToken.value;
    }
    return await globalThis.$fetch<T>(`${config.public.controlApiBase}${path}`, {
      ...(options.body === undefined ? {} : { body: options.body }),
      credentials: "include",
      headers,
      method,
      timeout: 5_000,
    });
  }

  return {
    csrfToken,
    request,
  };
}
