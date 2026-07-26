import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";

const root = join(import.meta.dirname, "../../..");
const apiUrl = "http://127.0.0.1:8015";
const webUrl = "http://127.0.0.1:5175";

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

export default async function globalSetup() {
  const directory = mkdtempSync(join(tmpdir(), "friday-e2e-"));
  const databaseUrl = `sqlite:///${join(directory, "friday.db")}`;
  const env = {
    ...process.env,
    FRIDAY_API_DATABASE_URL: databaseUrl,
    FRIDAY_API_HOST: "127.0.0.1",
    FRIDAY_API_PORT: "8015",
    FRIDAY_API_CORS_ORIGINS: webUrl,
    FRIDAY_WORKER_DATABASE_URL: databaseUrl,
    FRIDAY_WORKER_ID: "browser-e2e-worker",
    FRIDAY_WORKER_WORKSPACE_ROOT: directory,
    FRIDAY_COMPUTER_USE_ENABLED: "false",
    FRIDAY_MEMORY_ENABLED: "false",
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
      "8015",
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
      "5175",
    ],
    env,
  );
  await Promise.all([waitFor(`${apiUrl}/ready`, api), waitFor(webUrl, web)]);

  return async () => {
    for (const process of [web, worker, api]) process.kill("SIGTERM");
    await Promise.all(
      [web, worker, api].map(
        (process) =>
          new Promise<void>((resolve) => process.once("exit", () => resolve())),
      ),
    );
  };
}
