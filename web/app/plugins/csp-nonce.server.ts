export default defineNuxtPlugin(() => {
  const event = useRequestEvent();
  const cspNonce = event?.context.cspNonce;
  if (typeof cspNonce !== "string" || !cspNonce) {
    throw new Error("SSR response is missing its CSP nonce");
  }

  useHead({
    script: [
      {
        id: "pzp-csp-nonce",
        nonce: cspNonce,
        textContent: "{}",
        type: "application/json",
      },
    ],
  });
});
