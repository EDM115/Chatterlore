import type { components } from "~/types/openapi";

type ManagedChatPage = components["schemas"]["ManagedChatPage"];

interface ControlApiRequester {
  request<T>(path: string): Promise<T>;
}

export async function resolveQueryCatalogScope(api: ControlApiRequester): Promise<string | null> {
  const page = await api.request<ManagedChatPage>("/chats?limit=100");
  const activeChat = page.items.find((chat) => chat.status === "active");
  return activeChat ? String(activeChat.chat_id) : null;
}
