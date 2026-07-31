const SCRIPT_WITHOUT_NONCE = /<script\b(?![^>]*\bnonce=)/g;

export default defineNitroPlugin((nitroApp) => {
  nitroApp.hooks.hook("render:html", (html, { event }) => {
    const cspNonce = event.context.cspNonce;
    if (typeof cspNonce !== "string" || !cspNonce) {
      throw new Error("SSR response is missing its CSP nonce");
    }

    const nonceAttribute = `<script nonce="${cspNonce}"`;
    for (const chunks of [html.head, html.bodyPrepend, html.body, html.bodyAppend]) {
      for (let index = 0; index < chunks.length; index += 1) {
        chunks[index] = chunks[index].replace(SCRIPT_WITHOUT_NONCE, nonceAttribute);
      }
    }
  });
});
