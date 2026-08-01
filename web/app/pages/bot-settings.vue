<script setup lang="ts">
definePageMeta({ middleware: "auth" });

useSeoMeta({ title: "Bot Settings · PZP AI Control Plane" });

interface BotAdmin {
  telegram_user_id: number;
  source: "bootstrap" | "managed";
  version: number | null;
  granted_by: number | null;
  granted_at: string | null;
}

interface RuntimeSetting {
  key: string;
  value: unknown;
  default_value: unknown;
  previous_value: unknown;
  updated_by: number | null;
  updated_at: string | null;
  version: number | null;
}

const api = useControlApi();
const controlSession = useControlSession();
const admins = ref<BotAdmin[]>([]);
const runtimeSettings = ref<RuntimeSetting[]>([]);
const runtimeDraft = ref<Record<string, string>>({});
const newAdminId = ref("");
const activeOperation = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);

onMounted(async () => {
  if (!controlSession.hasCapability("bot.admin")) {
    await navigateTo("/unauthorized");
    return;
  }
  await refreshPage();
});

async function refreshPage() {
  error.value = null;
  try {
    [admins.value, runtimeSettings.value] = await Promise.all([
      api.request<BotAdmin[]>("/bot-settings/admins"),
      api.request<RuntimeSetting[]>("/bot-settings/runtime"),
    ]);
    runtimeDraft.value = Object.fromEntries(
      runtimeSettings.value.map((setting) => [setting.key, formatDraft(setting.value)]),
    );
  } catch {
    error.value = "Bot settings could not be loaded.";
  }
}

async function addBotAdmin() {
  const telegramUserId = Number(newAdminId.value);
  if (!Number.isSafeInteger(telegramUserId) || telegramUserId < 1) {
    error.value = "Enter a positive Telegram user ID.";
    return;
  }
  activeOperation.value = "admin:add";
  error.value = null;
  try {
    const created = await api.request<BotAdmin>("/bot-settings/admins", {
      body: { telegram_user_id: telegramUserId },
      method: "POST",
    });
    admins.value = [
      ...admins.value.filter((admin) => admin.telegram_user_id !== telegramUserId),
      created,
    ].sort((left, right) => left.telegram_user_id - right.telegram_user_id);
    newAdminId.value = "";
    notice.value = `Telegram ${telegramUserId} is now a bot administrator.`;
  } catch {
    error.value = "The bot administrator could not be added.";
  } finally {
    activeOperation.value = null;
  }
}

async function removeBotAdmin(admin: BotAdmin) {
  if (admin.version === null) return;
  if (!(await controlSession.ensureStepUp("bot_admin:delete", "/bot-settings"))) return;
  activeOperation.value = `admin:remove:${admin.telegram_user_id}`;
  error.value = null;
  try {
    await api.request(`/bot-settings/admins/${admin.telegram_user_id}`, {
      body: { version: admin.version },
      method: "DELETE",
    });
    admins.value = admins.value.filter(
      (candidate) => candidate.telegram_user_id !== admin.telegram_user_id,
    );
    notice.value = `Telegram ${admin.telegram_user_id} is no longer a bot administrator.`;
  } catch {
    error.value = "The bot administrator could not be removed. Refresh before retrying.";
  } finally {
    activeOperation.value = null;
  }
}

async function saveRuntimeSetting(setting: RuntimeSetting) {
  activeOperation.value = `runtime:${setting.key}`;
  error.value = null;
  try {
    runtimeSettings.value = await api.request<RuntimeSetting[]>("/bot-settings/runtime", {
      body: {
        updates: { [setting.key]: parseDraft(runtimeDraft.value[setting.key] ?? "", setting) },
      },
      method: "PATCH",
    });
    runtimeDraft.value = Object.fromEntries(
      runtimeSettings.value.map((item) => [item.key, formatDraft(item.value)]),
    );
    notice.value = `${setting.key} was updated and reloaded.`;
  } catch {
    error.value = `${setting.key} could not be updated.`;
  } finally {
    activeOperation.value = null;
  }
}

function formatDraft(value: unknown): string {
  if (value === null || value === undefined) return "off";
  return String(value);
}

function parseDraft(value: string, setting: RuntimeSetting): unknown {
  const clean = value.trim();
  const reference = setting.value ?? setting.default_value;
  if (typeof reference === "boolean") {
    if (["true", "1", "yes", "on"].includes(clean.toLowerCase())) return true;
    if (["false", "0", "no", "off"].includes(clean.toLowerCase())) return false;
    throw new Error("invalid boolean");
  }
  if (typeof reference === "number") return Number(clean);
  if (clean.toLowerCase() === "off" && setting.default_value === null) return null;
  return clean;
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="bot-settings-title">
      <div>
        <p class="eyebrow">Bot-wide authority</p>
        <h1 id="bot-settings-title">Owners and safe runtime controls.</h1>
        <p class="page-lede">
          Bootstrap owners remain deployment-managed. Reloadable administrators and the existing
          safe runtime settings apply without restarting the bot; provider secrets stay on the
          Providers page.
        </p>
      </div>
      <UButton color="neutral" variant="soft" @click="refreshPage">Refresh</UButton>
    </section>

    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="operation-notice" aria-live="polite">{{ notice }}</p>

    <section class="management-panel" aria-labelledby="bot-admins-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Authorization</p>
          <h2 id="bot-admins-title">Bot administrators</h2>
        </div>
        <span>{{ admins.length }} effective</span>
      </div>
      <form class="inline-form" @submit.prevent="addBotAdmin">
        <label for="new-bot-admin">Telegram user ID</label>
        <input
          id="new-bot-admin"
          v-model="newAdminId"
          data-testid="new-bot-admin"
          inputmode="numeric"
          autocomplete="off"
        />
        <UButton
          data-testid="add-bot-admin"
          type="button"
          :loading="activeOperation === 'admin:add'"
          @click="addBotAdmin"
          >Add administrator</UButton
        >
      </form>
      <div class="management-list">
        <article v-for="admin in admins" :key="admin.telegram_user_id" class="management-card">
          <div>
            <strong>Telegram {{ admin.telegram_user_id }}</strong
            ><small>{{
              admin.source === "bootstrap" ? "Bootstrap owner" : `Managed grant v${admin.version}`
            }}</small>
          </div>
          <UBadge :color="admin.source === 'bootstrap' ? 'neutral' : 'primary'" variant="soft">{{
            admin.source
          }}</UBadge>
          <UButton
            v-if="admin.version !== null"
            color="error"
            variant="soft"
            size="sm"
            :loading="activeOperation === `admin:remove:${admin.telegram_user_id}`"
            @click="removeBotAdmin(admin)"
            >Remove</UButton
          >
        </article>
      </div>
    </section>

    <section class="management-panel" aria-labelledby="runtime-settings-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Reloadable configuration</p>
          <h2 id="runtime-settings-title">Safe runtime settings</h2>
        </div>
        <span>{{ runtimeSettings.length }} allowlisted keys</span>
      </div>
      <div class="settings-list">
        <article v-for="setting in runtimeSettings" :key="setting.key" class="setting-row">
          <label :for="`runtime-${setting.key}`"
            ><strong>{{ setting.key }}</strong
            ><small
              >Default: {{ formatDraft(setting.default_value) }} ·
              {{ setting.version === null ? "environment" : `v${setting.version}` }}</small
            ></label
          >
          <input
            :id="`runtime-${setting.key}`"
            v-model="runtimeDraft[setting.key]"
            :data-testid="`runtime-${setting.key}`"
            autocomplete="off"
          />
          <UButton
            :data-testid="`save-runtime-${setting.key}`"
            size="sm"
            color="neutral"
            variant="soft"
            :loading="activeOperation === `runtime:${setting.key}`"
            @click="saveRuntimeSetting(setting)"
            >Save</UButton
          >
        </article>
      </div>
    </section>
  </ProtectedPageGate>
</template>
