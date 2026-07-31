interface CredentialRevealResponse {
  credential_id: number;
  value: string;
  expires_at: string;
}

const REVEAL_LIFETIME_MS = 30_000;

export function useSensitiveReveal() {
  const api = useControlApi();
  const route = useRoute();
  const session = useControlSession();
  const value = ref<string | null>(null);
  const revealedCredentialId = ref<number | null>(null);
  let clearTimer: ReturnType<typeof setTimeout> | undefined;

  const revealed = computed(() => value.value !== null);

  function clear() {
    if (clearTimer !== undefined) {
      clearTimeout(clearTimer);
      clearTimer = undefined;
    }
    value.value = null;
    revealedCredentialId.value = null;
  }

  async function reveal(profileName: string, credentialId: number): Promise<void> {
    clear();
    try {
      const response = await api.request<CredentialRevealResponse>(
        `/providers/${encodeURIComponent(profileName)}/credentials/${credentialId}/reveal`,
        { method: "POST" },
      );
      if (response.credential_id !== credentialId) throw new Error("Credential reveal mismatch");
      value.value = response.value;
      revealedCredentialId.value = credentialId;
      clearTimer = setTimeout(clear, REVEAL_LIFETIME_MS);
    } catch (error) {
      clear();
      throw error;
    }
  }

  async function copy(): Promise<boolean> {
    if (value.value === null || !navigator.clipboard) return false;
    await navigator.clipboard.writeText(value.value);
    return true;
  }

  function handleVisibilityChange() {
    if (document.visibilityState !== "visible") clear();
  }

  watch(() => route.fullPath, clear);
  watch(() => session.user.value?.telegram_user_id ?? null, clear);
  if (import.meta.client) {
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }
  onScopeDispose(() => {
    clear();
    if (import.meta.client) {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    }
  });

  return {
    clear,
    copy,
    reveal,
    revealed,
    revealedCredentialId,
    value,
  };
}

export function maskedCredential(lastFour: string | null): string {
  return `•••• •••• ${lastFour || "••••"}`;
}
