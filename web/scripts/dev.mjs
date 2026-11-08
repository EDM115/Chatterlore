import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const defaultWebRoot = resolve(scriptDirectory, "..");
const defaultRepositoryRoot = resolve(defaultWebRoot, "..");

export function developmentProcessSpecs({
  platform = process.platform,
  powershell = "pwsh.exe",
  repositoryRoot = defaultRepositoryRoot,
  webRoot = defaultWebRoot,
} = {}) {
  const windows = platform === "win32";
  const windowsCommand = ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command"];
  return [
    {
      name: "backend",
      command: windows ? powershell : "uv",
      args: windows
        ? [...windowsCommand, "uv run chatterlore control-plane-dev"]
        : ["run", "chatterlore", "control-plane-dev"],
      cwd: repositoryRoot,
    },
    {
      name: "web",
      command: windows ? powershell : "pnpm",
      args: windows ? [...windowsCommand, "pnpm dev:web"] : ["dev:web"],
      cwd: webRoot,
    },
  ];
}

export function terminateDevelopmentProcess(
  child,
  { platform = process.platform, signal = "SIGTERM", force = false, spawnProcess = spawn } = {},
) {
  const directSignal = force ? "SIGKILL" : signal;
  if (platform !== "win32" || !Number.isInteger(child.pid)) {
    child.kill(directSignal);
    return;
  }

  const taskkill = spawnProcess("taskkill.exe", ["/pid", String(child.pid), "/t", "/f"], {
    stdio: "ignore",
    windowsHide: true,
  });
  taskkill.once("error", () => child.kill(directSignal));
}

export async function coordinateDevelopmentProcesses({
  spawnProcess = spawn,
  specs = developmentProcessSpecs(),
  platform = process.platform,
  terminateProcess = terminateDevelopmentProcess,
  signalSource = process,
  stdout = process.stdout,
  stderr = process.stderr,
  shutdownTimeoutMs = 5_000,
} = {}) {
  const children = specs.map((spec) => {
    const child = spawnProcess(spec.command, spec.args, {
      cwd: spec.cwd,
      env: process.env,
      shell: false,
      stdio: ["inherit", "pipe", "pipe"],
      windowsHide: true,
    });
    prefixStream(child.stdout, stdout, `[${spec.name}] `);
    prefixStream(child.stderr, stderr, `[${spec.name}] `);
    return { child, spec };
  });

  return await new Promise((resolveResult) => {
    const exited = new Set();
    let resultCode = 1;
    let shuttingDown = false;
    let forceTimer;

    const running = ({ child }) => child.exitCode === null && child.signalCode === null;

    const finishIfStopped = () => {
      if (exited.size !== children.length) return;
      if (forceTimer !== undefined) clearTimeout(forceTimer);
      signalSource.off("SIGINT", onSigint);
      signalSource.off("SIGTERM", onSigterm);
      resolveResult(resultCode);
    };

    const beginShutdown = (signal, code, sourceIndex = -1) => {
      if (shuttingDown) return;
      shuttingDown = true;
      resultCode = code;
      children.forEach((entry, index) => {
        if (index !== sourceIndex && running(entry)) {
          terminateProcess(entry.child, { platform, signal });
        }
      });
      forceTimer = setTimeout(() => {
        for (const entry of children) {
          if (running(entry)) {
            terminateProcess(entry.child, { platform, signal: "SIGKILL", force: true });
          }
        }
      }, shutdownTimeoutMs);
      forceTimer.unref?.();
    };

    const onSigint = () => beginShutdown("SIGINT", 130);
    const onSigterm = () => beginShutdown("SIGTERM", 143);
    signalSource.on("SIGINT", onSigint);
    signalSource.on("SIGTERM", onSigterm);

    children.forEach(({ child, spec }, index) => {
      child.once("error", (error) => {
        stderr.write(
          `[${spec.name}] ${error instanceof Error ? error.message : "startup failed"}\n`,
        );
        beginShutdown("SIGTERM", 1, index);
        exited.add(index);
        finishIfStopped();
      });
      child.once("exit", (code) => {
        exited.add(index);
        if (!shuttingDown) beginShutdown("SIGTERM", code && code > 0 ? code : 1, index);
        finishIfStopped();
      });
    });
  });
}

function prefixStream(stream, target, prefix) {
  if (!stream) return;
  let buffered = "";
  stream.on("data", (chunk) => {
    buffered += chunk.toString();
    let newline = buffered.indexOf("\n");
    while (newline >= 0) {
      target.write(`${prefix}${buffered.slice(0, newline + 1)}`);
      buffered = buffered.slice(newline + 1);
      newline = buffered.indexOf("\n");
    }
  });
  stream.on("end", () => {
    if (buffered) target.write(`${prefix}${buffered}${buffered.endsWith("\n") ? "" : "\n"}`);
    buffered = "";
  });
}

async function main() {
  process.exitCode = await coordinateDevelopmentProcesses();
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : "development coordinator failed");
    process.exitCode = 1;
  });
}
