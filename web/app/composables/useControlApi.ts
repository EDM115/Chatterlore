type ControlApiMethod = "GET" | "POST" | "PATCH" | "DELETE";

interface ControlApiOptions {
  method?: ControlApiMethod;
  body?: unknown;
  signal?: AbortSignal;
  timeout?: number;
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
    try {
      return await globalThis.$fetch<T>(`${config.public.controlApiBase}${path}`, {
        ...(options.body === undefined ? {} : { body: options.body }),
        ...(options.signal === undefined ? {} : { signal: options.signal }),
        credentials: "include",
        headers,
        method,
        timeout: options.timeout ?? 5_000,
      });
    } catch (error) {
      if (controlResponseStatus(error) === 401) {
        useState("control-plane-session").value = null;
        useState("control-plane-session-status").value = "anonymous";
        csrfToken.value = null;
        if (import.meta.client && !import.meta.dev && !import.meta.test) {
          queueMicrotask(() => void navigateTo({ path: "/login", query: { expired: "1" } }));
        }
      }
      throw error;
    }
  }

  return {
    csrfToken,
    request,
  };
}
