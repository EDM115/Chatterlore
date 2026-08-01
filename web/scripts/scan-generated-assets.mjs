import { readdir, readFile } from "node:fs/promises";
import { dirname, extname, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const TEXT_EXTENSIONS = new Set([
  ".css",
  ".html",
  ".js",
  ".json",
  ".map",
  ".mjs",
  ".svg",
  ".txt",
  ".webmanifest",
  ".xml",
]);

const SERVER_ONLY_KEYS = [
  "AI_PROVIDER_PROFILES",
  "CONTROL_PLANE_ACTIVE_MASTER_KEY_ID",
  "CONTROL_PLANE_MASTER_KEYS",
  "DB_URL",
  "DATABASE_URL",
  "OPENAI_API_KEY",
  "PZP_API_DEV_ORIGIN",
  "TELEGRAM_BOT_TOKEN",
  "TELEGRAM_OIDC_CLIENT_SECRET",
  "WEBHOOK_SECRET",
];
const DEVELOPMENT_ONLY_MARKERS = [
  "CONTROL_PLANE_DEV_USER_ID",
  "/api/control/v1/auth/dev",
  "/api/control/v1/auth/dev/step-up",
];

const SECRET_KEY_PATTERN = /(?:API[_-]?KEY|MASTER[_-]?KEY|PASSWORD|SECRET|TOKEN|WEBHOOK)/i;
const STATIC_SECRET_PATTERNS = [
  {
    label: "database connection URL",
    pattern: /\b(?:mariadb|mysql|postgres(?:ql)?|sqlite(?:\+aiosqlite)?):\/\/[^\s"'<>]+/giu,
  },
  {
    label: "OpenAI-style API credential",
    pattern: /\bsk-[a-z0-9_-]{12,}/giu,
  },
  {
    label: "private key material",
    pattern: /-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----/gu,
  },
];

function unquote(value) {
  const trimmed = value.trim();
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function parseEnvFileValues(content) {
  const values = [];
  for (const rawLine of content.split(/\r?\n/u)) {
    const line = rawLine.trim().replace(/^export\s+/u, "");
    if (!line || line.startsWith("#")) continue;

    const separator = line.indexOf("=");
    if (separator < 1) continue;

    const value = unquote(line.slice(separator + 1));
    if (value.length >= 8) values.push(value);
  }
  return values;
}

function isSensitiveOrServerOnlyKey(key) {
  return (
    SECRET_KEY_PATTERN.test(key) ||
    key.endsWith("_URL") ||
    key.endsWith("_ORIGIN") ||
    SERVER_ONLY_KEYS.includes(key)
  );
}

async function existingSensitiveValues(environment, envFiles) {
  const values = [];
  for (const [key, value] of Object.entries(environment)) {
    if (typeof value === "string" && value.length >= 8 && isSensitiveOrServerOnlyKey(key)) {
      values.push(value);
    }
  }

  for (const envFile of envFiles) {
    try {
      values.push(...parseEnvFileValues(await readFile(envFile, "utf8")));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return [...new Set(values)];
}

async function generatedTextFiles(root) {
  const files = [];
  const entries = await readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await generatedTextFiles(path)));
    } else if (entry.isFile() && TEXT_EXTENSIONS.has(extname(entry.name).toLowerCase())) {
      files.push(path);
    }
  }
  return files;
}

export async function scanGeneratedAssets(
  outputRoot,
  { environment = process.env, envFiles = [] } = {},
) {
  const root = resolve(outputRoot);
  const secretValues = await existingSensitiveValues(environment, envFiles);
  const files = await generatedTextFiles(root);
  const leaks = [];

  for (const file of files) {
    const content = await readFile(file, "utf8");
    const displayPath = relative(root, file).replaceAll("\\", "/");

    for (const key of SERVER_ONLY_KEYS) {
      if (content.includes(key)) leaks.push(`${displayPath}: server-only configuration key`);
    }
    for (const marker of DEVELOPMENT_ONLY_MARKERS) {
      if (content.includes(marker)) leaks.push(`${displayPath}: development-only marker`);
    }
    for (const secret of secretValues) {
      if (content.includes(secret)) {
        leaks.push(`${displayPath}: environment or server-only value`);
      }
    }
    for (const { label, pattern } of STATIC_SECRET_PATTERNS) {
      pattern.lastIndex = 0;
      if (pattern.test(content)) leaks.push(`${displayPath}: ${label}`);
    }
  }

  if (leaks.length) {
    throw new Error(`generated asset secret scan failed\n${[...new Set(leaks)].join("\n")}`);
  }
  return { filesScanned: files.length };
}

async function main() {
  const scriptDir = dirname(fileURLToPath(import.meta.url));
  const repositoryRoot = resolve(scriptDir, "..", "..");
  const outputRoot = process.argv[2] ?? resolve(scriptDir, "..", ".output");
  const result = await scanGeneratedAssets(outputRoot, {
    envFiles: [resolve(repositoryRoot, ".env"), resolve(scriptDir, "..", ".env")],
  });
  console.log(`Generated asset secret scan passed (${result.filesScanned} text assets).`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : "generated asset secret scan failed");
    process.exitCode = 1;
  });
}
