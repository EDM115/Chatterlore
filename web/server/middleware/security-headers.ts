import { randomBytes } from "node:crypto";

const BACKEND_PATHS = ["/api", "/auth", "/telegram/webhook", "/health"];

export default defineEventHandler((event) => {
  const path = getRequestURL(event).pathname;
  if (BACKEND_PATHS.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) return;
  if (path.startsWith("/_nuxt/")) return;

  const cspNonce = randomBytes(18).toString("base64");
  event.context.cspNonce = cspNonce;

  setResponseHeaders(event, {
    "Cache-Control": "private, no-store",
    "Content-Security-Policy": `default-src 'self'; base-uri 'none'; object-src 'none'; script-src 'self' 'nonce-${cspNonce}' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'self' https://telegram.org https://*.telegram.org`,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    Pragma: "no-cache",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
  });
});
