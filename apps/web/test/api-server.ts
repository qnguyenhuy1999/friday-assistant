import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export interface TestApiServer {
  baseUrl: string;
  stop: () => Promise<void>;
}
const repoRoot = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
);

async function reservePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string")
        return reject(new Error("Could not reserve a port"));
      server.close(() => resolve(address.port));
    });
  });
}
function migrate(databaseUrl: string): void {
  const script =
    "from alembic import command; from alembic.config import Config; import sys; c=Config('alembic.ini'); c.set_main_option('script_location','migrations'); c.set_main_option('sqlalchemy.url',sys.argv[1]); command.upgrade(c,'head')";
  execFileSync("uv", ["run", "python", "-c", script, databaseUrl], {
    cwd: repoRoot,
    stdio: "inherit",
  });
}
async function waitForHealth(baseUrl: string): Promise<void> {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(`${baseUrl}/health`)).ok) return;
    } catch {
      /* booting */
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("API test server did not become healthy");
}
export async function startTestApiServer(): Promise<TestApiServer> {
  const dir = mkdtempSync(join(tmpdir(), "friday-api-test-"));
  const databaseUrl = `sqlite:///${join(dir, "test.db")}`;
  const port = await reservePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  migrate(databaseUrl);
  const child: ChildProcess = spawn(
    "uv",
    ["run", "python", "-m", "apps.api.main"],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        FRIDAY_API_DATABASE_URL: databaseUrl,
        FRIDAY_API_HOST: "127.0.0.1",
        FRIDAY_API_PORT: String(port),
        FRIDAY_API_SSE_POLL_INTERVAL_SECONDS: "0.1",
      },
      stdio: "inherit",
    },
  );
  const stop = async () => {
    child.kill("SIGTERM");
    rmSync(dir, { recursive: true, force: true });
  };
  try {
    await waitForHealth(baseUrl);
  } catch (error) {
    await stop();
    throw error;
  }
  return { baseUrl, stop };
}
