import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

function webUrl(): string {
  const value = process.env.FRIDAY_E2E_WEB_URL;
  if (!value) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");
  return value;
}

function apiUrl(): string {
  const value = process.env.FRIDAY_E2E_API_URL;
  if (!value) throw new Error("FRIDAY_E2E_API_URL was not set by global setup");
  return value;
}

async function detailValue(page: Page, label: RegExp): Promise<string> {
  return (
    (await page
      .locator("dt")
      .filter({ hasText: label })
      .locator("xpath=following-sibling::dd[1]")
      .textContent()) ?? ""
  ).trim();
}

async function createActiveAgent(
  page: Page,
  displayName: string,
  key: string,
): Promise<{ id: string; revisionId: string }> {
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByLabel("Key").fill(key);
  await page.getByLabel("Display name").fill(displayName);
  await page
    .getByLabel("Description")
    .fill("A real Task execution-target E2E Agent.");
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(page.getByRole("heading", { name: displayName })).toBeVisible();

  const id = await detailValue(page, /^Agent ID$/);
  await page
    .getByLabel("Instructions")
    .fill("Complete the assigned Task safely.");
  await page.getByLabel("Runtime kind").fill("claude_cli");
  await page.getByLabel("Runtime configuration (JSON object)").fill("{}");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v1/);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 — active")).toBeVisible();
  const revisionId = await detailValue(page, /^Selected revision pointer$/);
  return { id, revisionId };
}

async function waitForFrozenAgent(
  page: Page,
  runId: string,
  agentId: string,
  revisionId: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${apiUrl()}/v1/runs/${runId}/agent`,
        );
        if (!response.ok()) return null;
        return response.json();
      },
      { timeout: 30_000 },
    )
    .toMatchObject({
      resolved: true,
      agent_id: agentId,
      revision_id: revisionId,
    });
}

async function waitForFrozenWorkflow(
  page: Page,
  runId: string,
  workflowId: string,
  revisionId: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${apiUrl()}/v1/runs/${runId}/workflow`,
        );
        if (!response.ok()) return null;
        return response.json();
      },
      { timeout: 30_000 },
    )
    .toMatchObject({
      workflow_id: workflowId,
      workflow_revision_id: revisionId,
    });
}

test("binds an Agent, freezes its provenance, and preserves it after Task unbind", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const taskTitle = `Agent target E2E ${suffix}`;
  await page.goto(webUrl());
  const agent = await createActiveAgent(
    page,
    `E2E Task Agent ${suffix}`,
    `e2e-task-agent-${suffix}`,
  );

  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page.getByLabel("Title").fill(taskTitle);
  await page.getByRole("button", { name: "Create task" }).click();
  const taskRow = page.getByRole("listitem").filter({ hasText: taskTitle });
  await expect(taskRow).toBeVisible();
  await taskRow.getByRole("button", { name: taskTitle, exact: true }).click();
  await expect(page.getByRole("heading", { name: taskTitle })).toBeVisible();

  await page.getByLabel("Agent target", { exact: true }).selectOption(agent.id);
  await page.getByRole("button", { name: "Bind selected Agent" }).click();
  await expect(
    page.getByRole("heading", { name: "Agent", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(agent.id, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Start Run", exact: true }).click();
  await expect(page).toHaveURL(/\?view=run&id=/);
  const runId = new URL(page.url()).searchParams.get("id");
  if (!runId) throw new Error("Task Detail did not navigate to a Run");

  await waitForFrozenAgent(page, runId, agent.id, agent.revisionId);
  await page.reload();
  await expect(page.getByText("Frozen Agent provenance")).toBeVisible();
  await expect(
    page
      .locator("dt")
      .filter({ hasText: /^Agent$/ })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(agent.id);
  await expect(
    page
      .locator("dt")
      .filter({ hasText: /^Frozen revision$/ })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(agent.revisionId);

  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page
    .getByRole("listitem")
    .filter({ hasText: taskTitle })
    .getByRole("button", { name: taskTitle, exact: true })
    .click();
  await page.getByRole("button", { name: "Clear Agent binding" }).click();
  await expect(
    page.getByRole("heading", { name: "Default Friday runtime" }),
  ).toBeVisible();

  await page.goto(`${webUrl()}/?view=run&id=${runId}`);
  await expect(page.getByText("Frozen Agent provenance")).toBeVisible();
  await expect(
    page
      .locator("dt")
      .filter({ hasText: /^Agent$/ })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(agent.id);
  await expect(
    page
      .locator("dt")
      .filter({ hasText: /^Frozen revision$/ })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(agent.revisionId);
});

test("binds a Workflow and exposes its frozen revision provenance", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  await page.goto(webUrl());
  const agent = await createActiveAgent(
    page,
    `E2E Workflow Target Agent ${suffix}`,
    `e2e-workflow-target-agent-${suffix}`,
  );

  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await page.getByLabel("Key").fill(`e2e-task-workflow-${suffix}`);
  await page.getByLabel("Display name").fill(`E2E Task Workflow ${suffix}`);
  await page
    .getByLabel("Description")
    .fill("A real Task Workflow execution-target E2E proof.");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(
    page.getByRole("heading", { name: `E2E Task Workflow ${suffix}` }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Add node" }).click();
  await page.getByLabel("Node key").fill("execute");
  await page.getByLabel("Target Agent").selectOption(agent.id);
  await page.getByLabel("Objective").fill("Execute the workflow task.");
  await page.getByLabel("Input payload (JSON)").fill("{}");
  await page
    .getByLabel("Expected output contract")
    .fill("A completed workflow task.");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(
    page.getByRole("article", { name: "Workflow revision v1" }),
  ).toBeVisible();
  await expect(page.getByText("Created revision v1")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 — active")).toBeVisible();
  const workflowId = await detailValue(page, /^Workflow ID$/);
  const workflowRevisionId = await detailValue(
    page,
    /^Selected revision pointer$/,
  );

  const taskTitle = `Workflow target E2E ${suffix}`;
  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page.getByLabel("Title").fill(taskTitle);
  await page.getByRole("button", { name: "Create task" }).click();
  await page
    .getByRole("listitem")
    .filter({ hasText: taskTitle })
    .getByRole("button", { name: taskTitle, exact: true })
    .click();
  await expect(page.getByRole("heading", { name: taskTitle })).toBeVisible();

  await page
    .getByLabel("Workflow target", { exact: true })
    .selectOption(workflowId);
  await page.getByRole("button", { name: "Bind selected Workflow" }).click();
  await expect(
    page.getByRole("heading", { name: "Workflow", exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Start Run", exact: true }).click();
  await expect(page).toHaveURL(/\?view=run&id=/);
  const runId = new URL(page.url()).searchParams.get("id");
  if (!runId) throw new Error("Task Detail did not navigate to a Run");

  await waitForFrozenWorkflow(page, runId, workflowId, workflowRevisionId);
  await page.reload();
  await expect(page.getByText("Frozen Workflow provenance")).toBeVisible();
  await expect(
    page
      .locator("dt")
      .filter({ hasText: /^Workflow ID$/ })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(workflowId);
  await expect(
    page
      .locator("dt")
      .filter({ hasText: /^Frozen Workflow revision$/ })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(workflowRevisionId);
});
