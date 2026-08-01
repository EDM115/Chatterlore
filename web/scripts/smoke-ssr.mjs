import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDirectory, "..");
const entrypoint = resolve(webRoot, ".output", "server", "index.mjs");
const port = process.env.PZP_SSR_SMOKE_PORT ?? "43173";
const origin = `http://127.0.0.1:${port}`;
const sentinels = [
  "phase-ssr-db-secret.example",
  "phase-ssr-telegram-secret",
  "phase-ssr-provider-secret",
];

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

function requireCspNonce(response, html, label) {
  const contentSecurityPolicy = response.headers.get("content-security-policy") ?? "";
  const scriptDirective = contentSecurityPolicy
    .split(";")
    .find((directive) => directive.trim().startsWith("script-src"));
  const nonceMatch = scriptDirective?.match(/'nonce-([^']+)'/);
  requireCondition(Boolean(nonceMatch), `${label} SSR HTML is missing its script nonce`);
  requireCondition(
    !scriptDirective?.includes("'unsafe-inline'"),
    `${label} SSR script policy permits every inline script`,
  );
  const nonce = nonceMatch?.[1] ?? "";
  requireCondition(html.includes(`nonce="${nonce}"`), `${label} SSR HTML omits its CSP nonce`);
  const inlineScriptTags = html.match(/<script\b(?![^>]*\bsrc=)[^>]*>/g) ?? [];
  requireCondition(inlineScriptTags.length > 0, `${label} SSR HTML has no bootstrap scripts`);
  requireCondition(
    inlineScriptTags.every((tag) => tag.includes(`nonce="${nonce}"`)),
    `${label} SSR HTML contains an inline script without its CSP nonce`,
  );
  return nonce;
}

async function waitUntilReady(child) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`SSR server exited with code ${child.exitCode}`);
    try {
      const response = await fetch(`${origin}/login`);
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  throw new Error("SSR server did not become ready within 30 seconds");
}

async function stop(child) {
  if (child.exitCode !== null) return;
  child.kill();
  await Promise.race([
    new Promise((resolveExit) => child.once("exit", resolveExit)),
    new Promise((resolveDelay) => setTimeout(resolveDelay, 5_000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

async function main() {
  await access(entrypoint);
  const child = spawn(process.execPath, [entrypoint], {
    cwd: webRoot,
    env: {
      ...process.env,
      DB_URL: `postgresql://${sentinels[0]}/pzp`,
      NITRO_HOST: "127.0.0.1",
      NITRO_PORT: port,
      NODE_ENV: "production",
      OPENAI_API_KEY: sentinels[2],
      PORT: port,
      TELEGRAM_BOT_TOKEN: sentinels[1],
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let serverOutput = "";
  child.stdout.on("data", (chunk) => {
    serverOutput = `${serverOutput}${chunk}`.slice(-4_000);
  });
  child.stderr.on("data", (chunk) => {
    serverOutput = `${serverOutput}${chunk}`.slice(-4_000);
  });

  try {
    await waitUntilReady(child);
    const [loginResponse, protectedResponse, providersResponse] = await Promise.all([
      fetch(`${origin}/login`),
      fetch(`${origin}/`),
      fetch(`${origin}/providers`),
    ]);
    const [loginHtml, protectedHtml, providersHtml] = await Promise.all([
      loginResponse.text(),
      protectedResponse.text(),
      providersResponse.text(),
    ]);

    requireCondition(loginResponse.status === 200, "login SSR response was not successful");
    requireCondition(protectedResponse.status === 200, "protected SSR response was not successful");
    requireCondition(providersResponse.status === 200, "providers SSR response was not successful");
    requireCondition(
      loginHtml.includes("Sign in with Telegram."),
      "login route was not server rendered",
    );
    requireCondition(
      protectedHtml.includes('data-testid="auth-pending"'),
      "protected route omitted the neutral access gate",
    );
    requireCondition(
      protectedHtml.includes("Checking Telegram access"),
      "protected route omitted its access status",
    );
    requireCondition(
      !protectedHtml.includes("Private operations, one clear surface."),
      "protected content leaked into the SSR document",
    );
    requireCondition(
      !protectedHtml.includes("Opening the control plane"),
      "obsolete SPA loading HTML remains in the SSR response",
    );
    requireCondition(
      providersHtml.includes('data-testid="auth-pending"'),
      "providers route omitted the neutral access gate",
    );
    requireCondition(
      !providersHtml.includes("Keys that fail without taking the bot down"),
      "provider controls leaked into the SSR document",
    );
    requireCondition(
      !providersHtml.includes("Primary key"),
      "provider metadata leaked into the SSR document",
    );
    requireCondition(
      loginResponse.headers.get("cache-control") === "private, no-store",
      "SSR HTML is missing its private no-store policy",
    );
    requireCondition(
      loginResponse.headers.get("referrer-policy") === "no-referrer",
      "SSR HTML is missing its referrer policy",
    );
    const loginNonce = requireCspNonce(loginResponse, loginHtml, "login");
    const protectedNonce = requireCspNonce(protectedResponse, protectedHtml, "protected");
    const providersNonce = requireCspNonce(providersResponse, providersHtml, "providers");
    requireCondition(loginNonce !== protectedNonce, "SSR responses reused one script nonce");
    requireCondition(
      providersNonce !== loginNonce && providersNonce !== protectedNonce,
      "providers SSR response reused a script nonce",
    );
    for (const sentinel of sentinels) {
      requireCondition(
        !loginHtml.includes(sentinel),
        "a deployment secret leaked into login SSR HTML",
      );
      requireCondition(
        !protectedHtml.includes(sentinel),
        "a deployment secret leaked into protected SSR HTML",
      );
      requireCondition(
        !providersHtml.includes(sentinel),
        "a deployment secret leaked into providers SSR HTML",
      );
    }
    console.log("SSR production smoke passed for public, overview and providers direct routes.");
  } catch (error) {
    if (serverOutput.trim()) console.error(serverOutput.trim());
    throw error;
  } finally {
    await stop(child);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : "SSR production smoke failed");
  process.exitCode = 1;
});
