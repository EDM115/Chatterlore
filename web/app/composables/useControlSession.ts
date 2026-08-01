export interface ControlPlaneUser {
  telegram_user_id: number;
  first_name: string;
  last_name: string | null;
  username: string | null;
  language_code: string | null;
  photo_url: string | null;
  is_premium: boolean | null;
}

export interface ControlPlaneSession {
  user: ControlPlaneUser;
  client_kind: "browser" | "mini_app";
  authenticated_at: string;
  step_up_at: string | null;
  expires_at: string;
  csrf_token: string;
  capabilities: string[];
  chat_capabilities: Record<string, string[]>;
}

type SessionStatus = "idle" | "loading" | "authenticated" | "anonymous" | "error";

interface StepUpResponse {
  step_up: boolean;
  verified_at: string;
  action: string;
}

export function useControlSession() {
  const api = useControlApi();
  const session = useState<ControlPlaneSession | null>("control-plane-session", () => null);
  const status = useState<SessionStatus>("control-plane-session-status", () => "idle");
  const error = useState<string | null>("control-plane-session-error", () => null);
  const bootstrapped = useState<boolean>("control-plane-session-bootstrapped", () => false);

  const user = computed(() => session.value?.user ?? null);
  const clientKind = computed(() => session.value?.client_kind ?? null);
  const capabilities = computed(() => session.value?.capabilities ?? []);

  function accept(next: ControlPlaneSession) {
    session.value = next;
    api.csrfToken.value = next.csrf_token;
    status.value = "authenticated";
    error.value = null;
  }

  function clear(nextStatus: SessionStatus = "anonymous") {
    session.value = null;
    api.csrfToken.value = null;
    status.value = nextStatus;
  }

  async function refresh(): Promise<boolean> {
    status.value = "loading";
    try {
      accept(await api.request<ControlPlaneSession>("/session"));
      return true;
    } catch (caught) {
      clear(responseStatus(caught) === 401 ? "anonymous" : "error");
      error.value =
        responseStatus(caught) === 401 ? null : "The control-plane session could not be loaded.";
      return false;
    }
  }

  async function exchangeMiniApp(initData: string): Promise<boolean> {
    status.value = "loading";
    try {
      accept(
        await api.request<ControlPlaneSession>("/auth/mini-app", {
          body: { init_data: initData },
          method: "POST",
        }),
      );
      return true;
    } catch {
      clear("error");
      error.value = "Telegram could not verify this Mini App launch.";
      return false;
    }
  }

  async function exchangeDevelopmentSession(): Promise<boolean> {
    status.value = "loading";
    try {
      accept(
        await api.request<ControlPlaneSession>("/auth/dev", {
          method: "POST",
        }),
      );
      return true;
    } catch {
      clear("error");
      error.value = "The development control-plane session could not be established.";
      return false;
    }
  }

  async function bootstrap(): Promise<boolean> {
    if (import.meta.server) return false;
    if (bootstrapped.value) return status.value === "authenticated";
    bootstrapped.value = true;
    const initData = import.meta.client ? window.Telegram?.WebApp.initData.trim() : "";
    if (initData) return await exchangeMiniApp(initData);
    const restored = await refresh();
    if (restored || status.value !== "anonymous") return restored;
    if (import.meta.dev || import.meta.test) return await exchangeDevelopmentSession();
    return false;
  }

  async function ensureStepUp(action: string, returnTo = "/"): Promise<boolean> {
    const initData = import.meta.client ? window.Telegram?.WebApp.initData.trim() : "";
    try {
      let verified: StepUpResponse;
      if (session.value?.client_kind === "mini_app" && initData) {
        verified = await api.request<StepUpResponse>("/auth/step-up/mini-app", {
          body: { action, init_data: initData },
          method: "POST",
        });
      } else if (import.meta.dev || import.meta.test) {
        verified = await api.request<StepUpResponse>("/auth/dev/step-up", {
          body: { action },
          method: "POST",
        });
      } else {
        if (import.meta.client) {
          window.location.assign(
            `/auth/telegram/step-up/start?action=${encodeURIComponent(action)}&return_to=${encodeURIComponent(returnTo)}`,
          );
        }
        return false;
      }
      if (!verified.step_up || verified.action !== action) throw new Error("Step-up mismatch");
      if (session.value) {
        session.value = { ...session.value, step_up_at: verified.verified_at };
      }
      return true;
    } catch {
      error.value = "Fresh Telegram verification could not be completed.";
      return false;
    }
  }

  async function logout(): Promise<void> {
    await api.request<void>("/auth/logout", { method: "POST" });
    clear();
    bootstrapped.value = true;
  }

  function hasCapability(capability: string): boolean {
    return capabilities.value.includes(capability);
  }

  function hasChatCapability(chatId: number, capability = "chat.admin"): boolean {
    return session.value?.chat_capabilities[String(chatId)]?.includes(capability) ?? false;
  }

  return {
    bootstrap,
    capabilities,
    clientKind,
    error,
    ensureStepUp,
    exchangeDevelopmentSession,
    exchangeMiniApp,
    hasCapability,
    hasChatCapability,
    logout,
    refresh,
    session,
    status,
    user,
  };
}

function responseStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object" || !("response" in error)) return undefined;
  const response = error.response;
  if (!response || typeof response !== "object" || !("status" in response)) return undefined;
  return typeof response.status === "number" ? response.status : undefined;
}
