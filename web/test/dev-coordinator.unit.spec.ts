// @vitest-environment node

import { EventEmitter } from "node:events";
import { resolve } from "node:path";
import { PassThrough } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import {
  coordinateDevelopmentProcesses,
  developmentProcessSpecs,
  terminateDevelopmentProcess,
} from "../scripts/dev.mjs";

class FakeChild extends EventEmitter {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  readonly killSignals: NodeJS.Signals[] = [];
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  refuseGracefulShutdown = false;

  constructor(readonly pid?: number) {
    super();
  }

  finish(code: number | null, signal: NodeJS.Signals | null = null) {
    if (this.exitCode !== null || this.signalCode !== null) return;
    this.exitCode = code;
    this.signalCode = signal;
    this.emit("exit", code, signal);
  }

  kill(signal: NodeJS.Signals = "SIGTERM") {
    this.killSignals.push(signal);
    if (!this.refuseGracefulShutdown || signal === "SIGKILL") {
      queueMicrotask(() => this.finish(signal === "SIGKILL" ? 137 : null, signal));
    }
    return true;
  }
}

function textSink() {
  let output = "";
  return {
    sink: { write: (chunk: string) => (output += chunk) },
    text: () => output,
  };
}

describe("developmentProcessSpecs", () => {
  it("uses fixed PowerShell 7 wrappers as stable Windows process-tree roots", () => {
    const repositoryRoot = resolve("C:/work/Chatterlore");
    const webRoot = resolve(repositoryRoot, "web");

    expect(
      developmentProcessSpecs({
        platform: "win32",
        repositoryRoot,
        webRoot,
        powershell: "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
      }),
    ).toEqual([
      {
        name: "backend",
        command: "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
        args: [
          "-NoLogo",
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          "uv run chatterlore control-plane-dev",
        ],
        cwd: repositoryRoot,
      },
      {
        name: "web",
        command: "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
        args: ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "pnpm dev:web"],
        cwd: webRoot,
      },
    ]);
  });
});

describe("terminateDevelopmentProcess", () => {
  it("terminates the complete Windows child process tree", () => {
    const child = new FakeChild(4242);
    const taskkill = new EventEmitter();
    const spawnProcess = vi.fn(() => taskkill);

    terminateDevelopmentProcess(child, {
      platform: "win32",
      signal: "SIGTERM",
      spawnProcess,
    });

    expect(spawnProcess).toHaveBeenCalledWith("taskkill.exe", ["/pid", "4242", "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    expect(child.killSignals).toEqual([]);
  });

  it("falls back to the direct signal when taskkill cannot start", () => {
    const child = new FakeChild(4242);
    const taskkill = new EventEmitter();

    terminateDevelopmentProcess(child, {
      platform: "win32",
      signal: "SIGTERM",
      spawnProcess: () => taskkill,
    });
    taskkill.emit("error", new Error("taskkill unavailable"));

    expect(child.killSignals).toEqual(["SIGTERM"]);
  });
});

describe("coordinateDevelopmentProcesses", () => {
  it("prefixes output and stops the sibling when one child fails", async () => {
    const children = [new FakeChild(), new FakeChild()];
    const output = textSink();
    const errors = textSink();
    let spawnIndex = 0;
    const running = coordinateDevelopmentProcesses({
      spawnProcess: () => children[spawnIndex++],
      specs: developmentProcessSpecs({
        platform: "linux",
        repositoryRoot: "/work/Chatterlore",
        webRoot: "/work/Chatterlore/web",
      }),
      signalSource: new EventEmitter(),
      stdout: output.sink,
      stderr: errors.sink,
      shutdownTimeoutMs: 20,
    });

    children[0].stdout.emit("data", Buffer.from("API ready\n"));
    children[1].stderr.emit("data", Buffer.from("Nuxt warning\n"));
    children[0].finish(7);

    await expect(running).resolves.toBe(7);
    expect(children[1].killSignals).toEqual(["SIGTERM"]);
    expect(output.text()).toContain("[backend] API ready\n");
    expect(errors.text()).toContain("[web] Nuxt warning\n");
  });

  it("treats an unexpected clean child exit as failure", async () => {
    const children = [new FakeChild(), new FakeChild()];
    let spawnIndex = 0;
    const running = coordinateDevelopmentProcesses({
      spawnProcess: () => children[spawnIndex++],
      specs: developmentProcessSpecs({
        platform: "linux",
        repositoryRoot: "/work/Chatterlore",
        webRoot: "/work/Chatterlore/web",
      }),
      signalSource: new EventEmitter(),
      stdout: textSink().sink,
      stderr: textSink().sink,
      shutdownTimeoutMs: 20,
    });

    children[1].finish(0);

    await expect(running).resolves.toBe(1);
    expect(children[0].killSignals).toEqual(["SIGTERM"]);
  });

  it("forwards SIGINT to both children and returns the conventional exit code", async () => {
    const children = [new FakeChild(), new FakeChild()];
    const signals = new EventEmitter();
    let spawnIndex = 0;
    const running = coordinateDevelopmentProcesses({
      spawnProcess: () => children[spawnIndex++],
      specs: developmentProcessSpecs({
        platform: "linux",
        repositoryRoot: "/work/Chatterlore",
        webRoot: "/work/Chatterlore/web",
      }),
      signalSource: signals,
      stdout: textSink().sink,
      stderr: textSink().sink,
      shutdownTimeoutMs: 20,
    });

    signals.emit("SIGINT");

    await expect(running).resolves.toBe(130);
    expect(children[0].killSignals).toEqual(["SIGINT"]);
    expect(children[1].killSignals).toEqual(["SIGINT"]);
  });

  it("force-kills a child that ignores graceful termination", async () => {
    const children = [new FakeChild(), new FakeChild()];
    children[1].refuseGracefulShutdown = true;
    let spawnIndex = 0;
    const running = coordinateDevelopmentProcesses({
      spawnProcess: () => children[spawnIndex++],
      specs: developmentProcessSpecs({
        platform: "linux",
        repositoryRoot: "/work/Chatterlore",
        webRoot: "/work/Chatterlore/web",
      }),
      signalSource: new EventEmitter(),
      stdout: textSink().sink,
      stderr: textSink().sink,
      shutdownTimeoutMs: 5,
    });

    children[0].finish(2);

    await expect(running).resolves.toBe(2);
    expect(children[1].killSignals).toEqual(["SIGTERM", "SIGKILL"]);
  });
});
