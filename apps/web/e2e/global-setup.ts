import { mkdtempSync, rmSync } from "node:fs";
import { createServer, type Server } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";

const root = join(import.meta.dirname, "../../..");

function command(
  name: string,
  args: string[],
  env: NodeJS.ProcessEnv,
): ChildProcess {
  return spawn(name, args, { cwd: root, env, stdio: "pipe" });
}

async function waitFor(url: string, process: ChildProcess): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (process.exitCode !== null)
      throw new Error(`${url} process exited early`);
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // The process has not bound its loopback port yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function reservePort(): Promise<{ server: Server; port: number }> {
  const server = createServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("Failed to reserve an E2E port");
  return { server, port: address.port };
}

async function dynamicPorts(): Promise<[number, number]> {
  const first = await reservePort();
  const second = await reservePort();
  await Promise.all(
    [first.server, second.server].map(
      (server) =>
        new Promise<void>((resolve, reject) =>
          server.close((error) => (error ? reject(error) : resolve())),
        ),
    ),
  );
  return [first.port, second.port];
}

async function stop(processes: ChildProcess[]): Promise<void> {
  for (const process of processes)
    if (process.exitCode === null) process.kill("SIGTERM");
  await Promise.all(
    processes.map((process) =>
      process.exitCode !== null
        ? Promise.resolve()
        : new Promise<void>((resolve) => process.once("exit", () => resolve())),
    ),
  );
}

export default async function globalSetup() {
  const directory = mkdtempSync(join(tmpdir(), "friday-e2e-"));
  const [apiPort, webPort] = await dynamicPorts();
  const apiUrl = `http://127.0.0.1:${apiPort}`;
  const webUrl = `http://127.0.0.1:${webPort}`;
  process.env.FRIDAY_E2E_WEB_URL = webUrl;
  const databaseUrl = `sqlite:///${join(directory, "friday.db")}`;
  const env = {
    ...process.env,
    FRIDAY_API_DATABASE_URL: databaseUrl,
    FRIDAY_API_HOST: "127.0.0.1",
    FRIDAY_API_PORT: String(apiPort),
    FRIDAY_API_CORS_ORIGINS: webUrl,
    FRIDAY_WORKER_DATABASE_URL: databaseUrl,
    FRIDAY_WORKER_ID: "browser-e2e-worker",
    FRIDAY_WORKER_WORKSPACE_ROOT: directory,
    FRIDAY_COMPUTER_USE_ENABLED: "false",
    FRIDAY_MEMORY_ENABLED: "false",
    FRIDAY_E2E_BRAIN: "approval",
    VITE_API_BASE_URL: apiUrl,
  };
  const migration = spawnSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      "import os; from alembic import command; from alembic.config import Config; c = Config('alembic.ini'); c.set_main_option('sqlalchemy.url', os.environ['FRIDAY_API_DATABASE_URL']); command.upgrade(c, 'head')",
    ],
    {
      cwd: root,
      env,
      encoding: "utf8",
    },
  );
  if (migration.status !== 0)
    throw new Error(
      `E2E migration failed: ${migration.stderr || migration.stdout}`,
    );

  const api = command(
    "uv",
    [
      "run",
      "uvicorn",
      "apps.api.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(apiPort),
    ],
    env,
  );
  const worker = command(
    "uv",
    ["run", "python", "-m", "apps.worker.e2e_main"],
    env,
  );
  const web = command(
    "pnpm",
    [
      "--filter",
      "@friday/web",
      "exec",
      "vite",
      "--host",
      "127.0.0.1",
      "--port",
      String(webPort),
    ],
    env,
  );
  const processes = [web, worker, api];
  try {
    await Promise.all([waitFor(`${apiUrl}/ready`, api), waitFor(webUrl, web)]);
  } catch (error) {
    await stop(processes);
    rmSync(directory, { recursive: true, force: true });
    throw error;
  }

  return async () => {
    await stop(processes);
    rmSync(directory, { recursive: true, force: true });
  };
}
