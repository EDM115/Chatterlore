export default defineNuxtRouteMiddleware(async (to) => {
  if (import.meta.server) return;
  const controlSession = useControlSession();
  const authenticated = await controlSession.bootstrap();
  if (authenticated) return;
  return navigateTo({
    path: "/login",
    query: { return_to: safeReturnTo(to.fullPath) },
  });
});

function safeReturnTo(value: string): string {
  return value.startsWith("/") && !value.startsWith("//") && !value.includes("\\") ? value : "/";
}
