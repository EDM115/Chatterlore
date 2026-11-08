<script setup lang="ts">
import type { components } from "~/types/openapi";
import {
  isOperationProfileSetting,
  availableRouteProfile,
  requiresEmbeddingMigration,
  routeSettingLabel,
} from "~/utils/routeSettings";

definePageMeta({ middleware: "auth" });

useSeoMeta({ title: "Bot Settings · Chatterlore Control Plane" });

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

interface RuntimeSettingsMutation {
  items: RuntimeSetting[];
  version: number;
  audit_event_id: number;
}

type QueryCatalog = components["schemas"]["QueryCatalogResponse"];

const api = useControlApi();
const controlSession = useControlSession();
const admins = ref<BotAdmin[]>([]);
const runtimeSettings = ref<RuntimeSetting[]>([]);
const runtimeDraft = ref<Record<string, string>>({});
const savedRuntimeDraft = ref<Record<string, string>>({});
const catalog = ref<QueryCatalog | null>(null);
const newAdminId = ref("");
const activeOperation = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const migrationRequired = ref(false);
const dirty = computed(
  () =>
    newAdminId.value.trim() !== "" ||
    JSON.stringify(runtimeDraft.value) !== JSON.stringify(savedRuntimeDraft.value),
);

watch(dirty, (value) => syncTelegramClosingConfirmation(window.Telegram?.WebApp, value));
onBeforeUnmount(() => syncTelegramClosingConfirmation(window.Telegram?.WebApp, false));

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
      runtimeSettings.value.map((setting) => [setting.key, formatRuntimeDraft(setting)]),
    );
    savedRuntimeDraft.value = { ...runtimeDraft.value };
    try {
      const catalogScope = await resolveQueryCatalogScope(api);
      catalog.value = catalogScope
        ? await api.request<QueryCatalog>(
            `/query/catalog?scope=${encodeURIComponent(catalogScope)}`,
          )
        : null;
    } catch {
      catalog.value = null;
    }
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Bot settings could not be loaded.");
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
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The bot administrator could not be added.");
  } finally {
    activeOperation.value = null;
  }
}

async function removeBotAdmin(admin: BotAdmin) {
  if (admin.version === null || activeOperation.value !== null) return;
  activeOperation.value = `admin:remove:${admin.telegram_user_id}`;
  error.value = null;
  notice.value = null;
  try {
    if (!(await controlSession.ensureStepUp("bot_admin:delete", "/bot-settings"))) {
      error.value =
        controlSession.error.value ?? "Fresh Telegram verification could not be completed.";
      return;
    }
    await api.request(`/bot-settings/admins/${admin.telegram_user_id}`, {
      body: { version: admin.version },
      method: "DELETE",
    });
    admins.value = admins.value.filter(
      (candidate) => candidate.telegram_user_id !== admin.telegram_user_id,
    );
    notice.value = `Telegram ${admin.telegram_user_id} is no longer a bot administrator.`;
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "The bot administrator could not be removed. Refresh before retrying.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function saveRuntimeSetting(setting: RuntimeSetting) {
  activeOperation.value = `runtime:${setting.key}`;
  error.value = null;
  migrationRequired.value = false;
  try {
    const mutation = await api.request<RuntimeSettingsMutation>("/bot-settings/runtime", {
      body: {
        updates: { [setting.key]: parseDraft(runtimeDraft.value[setting.key] ?? "", setting) },
        version: setting.version ?? 0,
      },
      method: "PATCH",
    });
    runtimeSettings.value = mutation.items;
    runtimeDraft.value = Object.fromEntries(
      runtimeSettings.value.map((item) => [item.key, formatRuntimeDraft(item)]),
    );
    savedRuntimeDraft.value = { ...runtimeDraft.value };
    notice.value = `${setting.key} was updated and reloaded.`;
  } catch (caught) {
    migrationRequired.value = requiresEmbeddingMigration(caught);
    error.value = migrationRequired.value
      ? "Existing memory needs an embedding-index migration before changing this route or model. Preview and build a replacement index, then activate it explicitly."
      : controlErrorMessage(caught, `${setting.key} could not be updated.`);
  } finally {
    activeOperation.value = null;
  }
}

function formatRuntimeDraft(setting: RuntimeSetting): string {
  if (isOperationProfileSetting(setting.key) && setting.value == null) return "";
  return formatDraft(setting.value);
}

function formatDraft(value: unknown): string {
  if (value === null || value === undefined) return "off";
  return String(value);
}

function parseDraft(value: string, setting: RuntimeSetting): unknown {
  const clean = value.trim();
  if (isOperationProfileSetting(setting.key)) return clean || null;
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
    <NuxtLink v-if="migrationRequired" to="/embedding-indexes">Open Embedding indexes</NuxtLink>
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
            :disabled="activeOperation !== null"
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
        <p>
          Answers, summaries and embeddings resolve independently. An empty operation provider uses
          the fallback provider. Changing a provider uses that profile's model unless an explicit
          model override applies. Embedding changes for existing memory require an explicit index
          migration.
        </p>
        <article v-for="setting in runtimeSettings" :key="setting.key" class="setting-row">
          <label :for="`runtime-${setting.key}`"
            ><strong>{{ routeSettingLabel(setting.key) }}</strong><small>{{ setting.key }}</small
            ><small
              >Default: {{ formatDraft(setting.default_value) }} · Previous:
              {{ formatDraft(setting.previous_value) }} ·
              {{ setting.version === null ? "environment" : `v${setting.version}` }}</small
            ></label
          >
          <select
            v-if="isOperationProfileSetting(setting.key) && catalog"
            :id="`runtime-${setting.key}`"
            v-model="runtimeDraft[setting.key]"
            :data-testid="`runtime-${setting.key}`"
          >
            <option value="">Use fallback provider</option>
            <option
              v-if="
                runtimeDraft[setting.key] &&
                !availableRouteProfile(catalog.profiles, setting.key, runtimeDraft[setting.key]!)
              "
              :value="runtimeDraft[setting.key]"
            >
              {{ runtimeDraft[setting.key] }} · unavailable or incompatible
            </option>
            <option
              v-for="profile in catalog.profiles.filter(
                (profile) =>
                  profile.enabled &&
                  profile.capabilities.includes(
                    setting.key === 'embedding_provider_profile' ? 'embedding' : 'chat',
                  ),
              )"
              :key="profile.name"
              :value="profile.name"
            >
              {{ profile.name }}
            </option>
          </select>
          <input
            v-else
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

    <section v-if="catalog" class="management-panel" aria-labelledby="approved-models-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Approved selections</p>
          <h2 id="approved-models-title">Model and reasoning allowlists</h2>
        </div>
        <span>Validated before provider calls</span>
      </div>
      <div class="settings-columns">
        <article>
          <h3>Chat models</h3>
          <p>{{ catalog.chat_models.join(" · ") || "No approved chat models" }}</p>
        </article>
        <article>
          <h3>Embedding models</h3>
          <p>{{ catalog.embedding_models.join(" · ") || "No approved embedding models" }}</p>
        </article>
        <article>
          <h3>Reasoning efforts</h3>
          <p>{{ catalog.reasoning_levels.join(" · ") || "No approved reasoning overrides" }}</p>
        </article>
      </div>
    </section>
  </ProtectedPageGate>
</template>
