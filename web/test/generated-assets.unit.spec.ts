// @vitest-environment node

import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { scanGeneratedAssets } from "../scripts/scan-generated-assets.mjs";

describe("scanGeneratedAssets", () => {
  it("accepts generated assets that contain only public control-plane configuration", async () => {
    const root = await mkdtemp(join(tmpdir(), "chatterlore-safe-assets-"));
    await mkdir(join(root, "_nuxt"));
    await writeFile(join(root, "index.html"), '<meta name="api-base" content="/api/control/v1">');
    await writeFile(join(root, "_nuxt", "app.js"), 'const label = "Sign in with Telegram"');

    await expect(scanGeneratedAssets(root, { environment: {}, envFiles: [] })).resolves.toEqual({
      filesScanned: 2,
    });
  });

  it("accepts public GitHub Actions service URLs in generated assets and bundled dependencies", async () => {
    const root = await mkdtemp(join(tmpdir(), "chatterlore-ci-assets-"));
    const environment = {
      GITHUB_SERVER_URL: "https://github.com",
      GITHUB_API_URL: "https://api.github.com",
      GITHUB_GRAPHQL_URL: "https://api.github.com/graphql",
    };
    await mkdir(join(root, "server", "node_modules", "dependency"), { recursive: true });
    await writeFile(join(root, "app.js"), JSON.stringify(Object.values(environment)));
    await writeFile(
      join(root, "server", "node_modules", "dependency", "package.json"),
      JSON.stringify({ repository: "https://github.com/example/dependency" }),
    );

    await expect(scanGeneratedAssets(root, { environment, envFiles: [] })).resolves.toEqual({
      filesScanned: 2,
    });
  });

  it.each([
    ["INTERNAL_SERVICE_URL", "https://github.com"],
    ["INTERNAL_SERVICE_ORIGIN", "https://api.github.com"],
    ["GITHUB_TOKEN", "ci-private-token-that-must-never-ship"],
  ])("still rejects sensitive environment values from %s", async (key, value) => {
    const root = await mkdtemp(join(tmpdir(), "chatterlore-sensitive-ci-assets-"));
    await mkdir(join(root, "server"));
    await writeFile(join(root, "server", "dependency.mjs"), JSON.stringify(value));

    await expect(
      scanGeneratedAssets(root, {
        environment: { GITHUB_SERVER_URL: "https://github.com", [key]: value },
        envFiles: [],
      }),
    ).rejects.toThrow("environment or server-only value");
  });

  it("rejects secret values and server-only configuration without echoing the secret", async () => {
    const root = await mkdtemp(join(tmpdir(), "chatterlore-leaking-assets-"));
    const envFile = join(root, "build.env");
    const secret = "phase2-never-print-this-telegram-secret";
    await writeFile(envFile, `TELEGRAM_BOT_TOKEN=${secret}\n`);
    await writeFile(join(root, "index.html"), `<script>window.leak="${secret}"</script>`);
    await writeFile(join(root, "app.js"), 'const serverSetting = "DB_URL"');

    let error: Error | undefined;
    try {
      await scanGeneratedAssets(root, { environment: {}, envFiles: [envFile] });
    } catch (caught) {
      error = caught as Error;
    }

    expect(error?.message).toContain("generated asset secret scan failed");
    expect(error?.message).toContain("environment or server-only value");
    expect(error?.message).toContain("server-only configuration key");
    expect(error?.message).not.toContain(secret);
  });

  it("rejects any non-trivial value loaded from an env file", async () => {
    const root = await mkdtemp(join(tmpdir(), "chatterlore-env-assets-"));
    const envFile = join(root, "build.env");
    const value = "phase2-internal-build-label";
    await writeFile(envFile, `INTERNAL_BUILD_LABEL=${value}\n`);
    await writeFile(join(root, "app.js"), `const leakedBuildLabel = "${value}"`);

    await expect(
      scanGeneratedAssets(root, { environment: {}, envFiles: [envFile] }),
    ).rejects.toThrow("environment or server-only value");
  });

  it("rejects development authentication markers from production output", async () => {
    const root = await mkdtemp(join(tmpdir(), "chatterlore-development-assets-"));
    await writeFile(
      join(root, "server.mjs"),
      'const key="CONTROL_PLANE_DEV_USER_ID"; fetch("/api/control/v1/auth/dev/step-up")',
    );

    await expect(scanGeneratedAssets(root, { environment: {}, envFiles: [] })).rejects.toThrow(
      "development-only marker",
    );
  });
});
