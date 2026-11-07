export function routeSettingLabel(key: string): string {
  const labels: Record<string, string> = {
    provider_profile: "Fallback provider",
    chat_provider_profile: "Answer provider",
    summary_provider_profile: "Summary provider",
    embedding_provider_profile: "Embedding provider",
    chat_model: "Answer model",
    summary_model: "Summary model",
    embedding_model: "Embedding model",
  };
  return labels[key] ?? key;
}

export function isOperationProfileSetting(key: string): boolean {
  return [
    "chat_provider_profile",
    "summary_provider_profile",
    "embedding_provider_profile",
  ].includes(key);
}

export function availableRouteProfile(
  profiles: { name: string; enabled: boolean; capabilities: string[] }[],
  key: string,
  name: string,
): boolean {
  return profiles.some(
    (profile) =>
      profile.name === name &&
      profile.enabled &&
      profile.capabilities.includes(key === "embedding_provider_profile" ? "embedding" : "chat"),
  );
}

export function requiresEmbeddingMigration(error: unknown): boolean {
  return ["embedding_migration_required", "migration_required"].includes(
    controlResponseCode(error) ?? "",
  );
}
