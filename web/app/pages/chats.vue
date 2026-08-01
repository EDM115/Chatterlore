<script setup lang="ts">
definePageMeta({ middleware: "auth" });

useSeoMeta({ title: "Chats · PZP AI Control Plane" });

interface ManagedChat {
  chat_id: number;
  title: string | null;
  username: string | null;
  status: "active" | "paused" | "detached";
  version: number;
  updated_at: string;
}

interface ChatAdmin {
  chat_id: number;
  telegram_user_id: number;
  version: number;
  granted_by: number;
  granted_at: string;
  active: boolean;
}

interface Enrollment {
  request_id: number;
  status: string;
  delivery: "mini_app" | "telegram_dm";
  expires_at: string;
  prepared_button_id: string | null;
  chat_id: number | null;
  error_code: string | null;
}

interface ScopeSettings {
  scope: string;
  effective: Record<string, unknown>;
  inherited: Record<string, unknown>;
  overrides: Record<string, unknown>;
  policy: { learning_enabled: boolean; allow_admins: boolean };
}

const api = useControlApi();
const controlSession = useControlSession();
const chats = ref<ManagedChat[]>([]);
const selectedChatId = ref<number | null>(null);
const chatAdmins = ref<ChatAdmin[]>([]);
const scopeSettings = ref<ScopeSettings | null>(null);
const threadId = ref("");
const newChatAdminId = ref("");
const settingsDraft = ref<Record<string, string>>({});
const learningEnabled = ref(false);
const allowAdmins = ref(false);
const confirmations = ref<Record<number, string>>({});
const activeOperation = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);

const isBotAdmin = computed(() => controlSession.hasCapability("bot.admin"));
const selectedChat = computed(
  () => chats.value.find((chat) => chat.chat_id === selectedChatId.value) ?? null,
);
const supportsRequestChat = computed(() => {
  if (!import.meta.client) return false;
  const webApp = window.Telegram?.WebApp;
  return Boolean(
    webApp?.requestChat && (!webApp.isVersionAtLeast || webApp.isVersionAtLeast("9.6")),
  );
});

onMounted(refreshChats);

async function refreshChats() {
  error.value = null;
  try {
    chats.value = await api.request<ManagedChat[]>("/chats");
    if (!chats.value.some((chat) => chat.chat_id === selectedChatId.value)) {
      selectedChatId.value = chats.value[0]?.chat_id ?? null;
    }
    await loadSelectedChat();
  } catch {
    error.value = "Managed chats could not be loaded.";
  }
}

async function loadSelectedChat() {
  if (selectedChatId.value === null) {
    chatAdmins.value = [];
    scopeSettings.value = null;
    return;
  }
  const query = threadId.value.trim()
    ? `?thread_id=${encodeURIComponent(threadId.value.trim())}`
    : "";
  [chatAdmins.value, scopeSettings.value] = await Promise.all([
    api.request<ChatAdmin[]>(`/chats/${selectedChatId.value}/admins`),
    api.request<ScopeSettings>(`/chats/${selectedChatId.value}/settings${query}`),
  ]);
  settingsDraft.value = Object.fromEntries(
    Object.entries(scopeSettings.value.overrides).map(([key, value]) => [key, formatDraft(value)]),
  );
  learningEnabled.value = scopeSettings.value.policy.learning_enabled;
  allowAdmins.value = scopeSettings.value.policy.allow_admins;
}

async function startEnrollment() {
  if (!isBotAdmin.value) return;
  activeOperation.value = "enrollment";
  error.value = null;
  notice.value = null;
  const delivery: Enrollment["delivery"] = supportsRequestChat.value ? "mini_app" : "telegram_dm";
  try {
    const pending = await api.request<Enrollment>("/chats/enrollment-requests", {
      body: { delivery },
      method: "POST",
    });
    if (delivery === "telegram_dm") {
      notice.value =
        "A group selection button was sent to your bot DM. Complete it there while this page checks the result.";
      void pollEnrollment(pending);
      return;
    }
    if (!pending.prepared_button_id || !window.Telegram?.WebApp.requestChat) {
      throw new Error("prepared request unavailable");
    }
    const sent = await new Promise<boolean>((resolve) => {
      window.Telegram?.WebApp.requestChat?.(pending.prepared_button_id as string, resolve);
    });
    if (!sent) {
      notice.value = "Telegram chat selection was cancelled.";
      return;
    }
    await pollEnrollment(pending);
  } catch {
    error.value = "Chat enrollment could not be started. Open the bot DM once, then retry.";
  } finally {
    activeOperation.value = null;
  }
}

async function pollEnrollment(pending: Enrollment) {
  const deadline = Math.min(new Date(pending.expires_at).getTime(), Date.now() + 60_000);
  let current = pending;
  while (current.status === "pending" && Date.now() <= deadline) {
    current = await api.request<Enrollment>(`/chats/enrollment-requests/${pending.request_id}`);
    if (current.status !== "pending") break;
    await delay(1_000);
  }
  if (current.status === "completed" && current.chat_id !== null) {
    notice.value = `Telegram chat ${current.chat_id} enrolled.`;
    await refreshChats();
  } else if (current.status !== "pending") {
    error.value = `Chat enrollment ended with status ${current.status}.`;
  }
}

async function changeStatus(chat: ManagedChat, status: ManagedChat["status"]) {
  activeOperation.value = `status:${chat.chat_id}`;
  try {
    const updated = await api.request<ManagedChat>(`/chats/${chat.chat_id}/status`, {
      body: { status, version: chat.version },
      method: "PATCH",
    });
    replaceChat(updated);
    notice.value = `${chatLabel(updated)} is now ${updated.status}.`;
  } catch {
    error.value = "The chat lifecycle changed concurrently. Refresh before retrying.";
  } finally {
    activeOperation.value = null;
  }
}

async function runConfirmedAction(chat: ManagedChat, action: "leave" | "forget") {
  const expected = `${action.toUpperCase()} ${chat.chat_id}`;
  if ((confirmations.value[chat.chat_id] ?? "").trim() !== expected) {
    error.value = `Type ${expected} exactly before continuing.`;
    return;
  }
  if (!(await controlSession.ensureStepUp(`chat:${action}`, "/chats"))) return;
  activeOperation.value = `${action}:${chat.chat_id}`;
  try {
    const result = await api.request<Record<string, unknown>>(`/chats/${chat.chat_id}/${action}`, {
      body: { confirmation: expected, version: chat.version },
      method: "POST",
    });
    if (action === "leave") replaceChat(result as unknown as ManagedChat);
    notice.value =
      action === "leave"
        ? `${chatLabel(chat)} was detached after the bot left.`
        : `${chatLabel(chat)} memory was forgotten and projections were reconciled.`;
    confirmations.value[chat.chat_id] = "";
  } catch {
    error.value = `The ${action} action could not be completed.`;
  } finally {
    activeOperation.value = null;
  }
}

async function addChatAdmin() {
  if (selectedChatId.value === null) return;
  const telegramUserId = Number(newChatAdminId.value);
  if (!Number.isSafeInteger(telegramUserId) || telegramUserId < 1) {
    error.value = "Enter a positive Telegram user ID.";
    return;
  }
  try {
    const created = await api.request<ChatAdmin>(`/chats/${selectedChatId.value}/admins`, {
      body: { telegram_user_id: telegramUserId },
      method: "POST",
    });
    chatAdmins.value = [
      ...chatAdmins.value.filter((admin) => admin.telegram_user_id !== telegramUserId),
      created,
    ];
    newChatAdminId.value = "";
  } catch {
    error.value = "Telegram could not verify that account in this chat.";
  }
}

async function removeChatAdmin(admin: ChatAdmin) {
  try {
    await api.request(`/chats/${admin.chat_id}/admins/${admin.telegram_user_id}`, {
      body: { version: admin.version },
      method: "DELETE",
    });
    chatAdmins.value = chatAdmins.value.filter(
      (candidate) => candidate.telegram_user_id !== admin.telegram_user_id,
    );
  } catch {
    error.value = "The delegated administrator could not be removed.";
  }
}

async function saveScopeSettings() {
  if (selectedChatId.value === null) return;
  const settings = Object.fromEntries(
    Object.entries(settingsDraft.value)
      .filter(([, value]) => value.trim() !== "")
      .map(([key, value]) => [key, parseScopeValue(key, value)]),
  );
  const query = threadId.value.trim()
    ? `?thread_id=${encodeURIComponent(threadId.value.trim())}`
    : "";
  try {
    scopeSettings.value = await api.request<ScopeSettings>(
      `/chats/${selectedChatId.value}/settings${query}`,
      {
        body: {
          allow_admins: allowAdmins.value,
          learning_enabled: learningEnabled.value,
          settings,
        },
        method: "PATCH",
      },
    );
    notice.value = `Settings for ${scopeSettings.value.scope} were applied.`;
  } catch {
    error.value = "The scoped settings could not be applied.";
  }
}

function replaceChat(updated: ManagedChat) {
  chats.value = chats.value.map((chat) => (chat.chat_id === updated.chat_id ? updated : chat));
}

function chatLabel(chat: ManagedChat): string {
  return chat.title ?? `Telegram chat ${chat.chat_id}`;
}

function formatDraft(value: unknown): string {
  return value === null || value === undefined ? "off" : String(value);
}

function parseScopeValue(key: string, value: string): unknown {
  const clean = value.trim();
  if (
    [
      "retrieval_top_k",
      "summary_batch_size",
      "raw_message_retention_days",
      "knowledge_retention_days",
    ].includes(key)
  ) {
    return clean.toLowerCase() === "off" ? null : Number(clean);
  }
  if (key === "review_before_compact")
    return ["true", "1", "yes", "on"].includes(clean.toLowerCase());
  return clean;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="chats-title">
      <div>
        <p class="eyebrow">Scoped administration</p>
        <h1 id="chats-title">Chats stay inside their own boundary.</h1>
        <p class="page-lede">
          Enroll groups through Telegram, pause learning without deleting memory, delegate
          chat-local administration and see exactly which settings are inherited or overridden.
        </p>
      </div>
      <UButton color="neutral" variant="soft" @click="refreshChats">Refresh</UButton>
    </section>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="operation-notice" aria-live="polite">{{ notice }}</p>

    <section
      v-if="isBotAdmin"
      class="management-panel enrollment-panel"
      aria-labelledby="enroll-title"
    >
      <div>
        <p class="eyebrow">Trusted enrollment</p>
        <h2 id="enroll-title">Add a Telegram group</h2>
        <p>
          Telegram performs the selection. The backend accepts only the matching short-lived
          <code>chat_shared</code> result and verifies your admin role plus the bot membership.
        </p>
      </div>
      <UButton
        data-testid="enroll-chat"
        :loading="activeOperation === 'enrollment'"
        @click="startEnrollment"
        >{{ supportsRequestChat ? "Select in Telegram" : "Send selection to bot DM" }}</UButton
      >
    </section>

    <section class="management-panel" aria-labelledby="managed-chats-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Lifecycle</p>
          <h2 id="managed-chats-title">Managed chats</h2>
        </div>
        <span>{{ chats.length }} visible</span>
      </div>
      <p v-if="!chats.length" class="empty-panel">
        No managed chats are visible to this Telegram account.
      </p>
      <div v-else class="management-list">
        <article
          v-for="chat in chats"
          :key="chat.chat_id"
          class="management-card chat-card"
          :data-selected="selectedChatId === chat.chat_id"
        >
          <button
            class="chat-selector"
            type="button"
            @click="
              selectedChatId = chat.chat_id;
              loadSelectedChat();
            "
          >
            <strong>{{ chatLabel(chat) }}</strong
            ><small
              >{{ chat.chat_id
              }}<template v-if="chat.username"> · @{{ chat.username }}</template></small
            >
          </button>
          <span class="health-pill" :data-state="chat.status">{{ chat.status }}</span>
          <div class="credential-actions">
            <UButton
              v-if="chat.status !== 'active'"
              size="xs"
              color="primary"
              variant="soft"
              @click="changeStatus(chat, 'active')"
              >Resume</UButton
            >
            <UButton
              v-if="chat.status === 'active'"
              size="xs"
              color="warning"
              variant="soft"
              @click="changeStatus(chat, 'paused')"
              >Pause</UButton
            >
            <UButton
              v-if="chat.status !== 'detached'"
              size="xs"
              color="neutral"
              variant="soft"
              @click="changeStatus(chat, 'detached')"
              >Detach</UButton
            >
          </div>
          <div v-if="isBotAdmin" class="confirmed-actions">
            <input
              v-model="confirmations[chat.chat_id]"
              :aria-label="`Confirmation for ${chat.chat_id}`"
              :placeholder="`LEAVE ${chat.chat_id} or FORGET ${chat.chat_id}`"
              autocomplete="off"
            />
            <UButton
              size="xs"
              color="error"
              variant="soft"
              @click="runConfirmedAction(chat, 'leave')"
              >Leave</UButton
            >
            <UButton
              size="xs"
              color="error"
              variant="solid"
              @click="runConfirmedAction(chat, 'forget')"
              >Forget memory</UButton
            >
          </div>
        </article>
      </div>
    </section>

    <template v-if="selectedChat">
      <section class="management-panel" aria-labelledby="delegated-admins-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Delegation</p>
            <h2 id="delegated-admins-title">Chat administrators</h2>
          </div>
          <span>{{ chatAdmins.length }} delegated</span>
        </div>
        <form class="inline-form" @submit.prevent="addChatAdmin">
          <label for="new-chat-admin">Telegram user ID</label
          ><input
            id="new-chat-admin"
            v-model="newChatAdminId"
            inputmode="numeric"
            autocomplete="off"
          /><UButton type="submit">Add for this chat</UButton>
        </form>
        <div class="management-list">
          <article
            v-for="admin in chatAdmins"
            :key="admin.telegram_user_id"
            class="management-card"
          >
            <div>
              <strong>Telegram {{ admin.telegram_user_id }}</strong
              ><small>Delegated grant v{{ admin.version }}</small>
            </div>
            <UButton size="sm" color="error" variant="soft" @click="removeChatAdmin(admin)"
              >Remove</UButton
            >
          </article>
        </div>
      </section>

      <section class="management-panel" aria-labelledby="scope-settings-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Precedence</p>
            <h2 id="scope-settings-title">Chat and topic settings</h2>
          </div>
          <span>{{ scopeSettings?.scope }}</span>
        </div>
        <form class="inline-form" @submit.prevent="loadSelectedChat">
          <label for="thread-id">Topic ID (optional)</label
          ><input id="thread-id" v-model="threadId" inputmode="numeric" /><UButton
            type="submit"
            color="neutral"
            variant="soft"
            >Load scope</UButton
          >
        </form>
        <div v-if="scopeSettings" class="settings-columns">
          <article>
            <h3>Effective</h3>
            <dl>
              <template v-for="(value, key) in scopeSettings.effective" :key="String(key)"
                ><dt>{{ key }}</dt>
                <dd>{{ formatDraft(value) }}</dd></template
              >
            </dl>
          </article>
          <article>
            <h3>Inherited</h3>
            <dl>
              <template v-for="(value, key) in scopeSettings.inherited" :key="String(key)"
                ><dt>{{ key }}</dt>
                <dd>{{ formatDraft(value) }}</dd></template
              >
            </dl>
          </article>
          <article>
            <h3>Overrides</h3>
            <div class="settings-list">
              <label v-for="(value, key) in scopeSettings.effective" :key="String(key)"
                ><span>{{ key }}</span
                ><input
                  v-model="settingsDraft[String(key)]"
                  :placeholder="
                    scopeSettings.inherited[String(key)] === undefined
                      ? formatDraft(value)
                      : `Inherited: ${formatDraft(scopeSettings.inherited[String(key)])}`
                  "
              /></label>
            </div>
          </article>
        </div>
        <div class="policy-controls">
          <label><input v-model="learningEnabled" type="checkbox" /> Learning enabled</label
          ><label><input v-model="allowAdmins" type="checkbox" /> Telegram admins may learn</label
          ><UButton @click="saveScopeSettings">Apply scope settings</UButton>
        </div>
      </section>
    </template>
  </ProtectedPageGate>
</template>
