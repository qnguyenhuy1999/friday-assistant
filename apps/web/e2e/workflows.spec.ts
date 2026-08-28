import { expect, test } from "@playwright/test";

test("an operator creates and re-enables a Workflow using the same revision", async ({
  page,
}) => {
  const url = process.env.FRIDAY_E2E_WEB_URL;
  if (!url) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");

  await page.goto(url);
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByLabel("Key").fill("e2e-workflow-agent-a");
  await page.getByLabel("Display name").fill("Workflow Agent A");
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(
    page.getByRole("heading", { name: "Workflow Agent A" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByLabel("Key").fill("e2e-workflow-agent-b");
  await page.getByLabel("Display name").fill("Workflow Agent B");
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(
    page.getByRole("heading", { name: "Workflow Agent B" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await page.getByLabel("Key").fill("e2e-operator-workflow");
  await page.getByLabel("Display name").fill("E2E Operator Workflow");
  await page
    .getByLabel("Description")
    .fill("A persisted browser operator proof.");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(
    page.getByRole("heading", { name: "E2E Operator Workflow" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Add node" }).click();
  await page.getByRole("button", { name: "Add node" }).click();
  const keys = page.getByLabel("Node key");
  await keys.nth(0).fill("analyze");
  await keys.nth(1).fill("implement");
  const agents = page.getByLabel("Target Agent");
  await agents.nth(0).selectOption({
    label:
      "Workflow Agent A · e2e-workflow-agent-a · active · no selected revision",
  });
  await agents.nth(1).selectOption({
    label:
      "Workflow Agent B · e2e-workflow-agent-b · active · no selected revision",
  });
  await page.getByLabel("Objective").nth(0).fill("Analyze the change.");
  await page.getByLabel("Objective").nth(1).fill("Implement the change.");
  await page
    .getByLabel("Input payload (JSON)")
    .nth(0)
    .fill('{"repository":"friday"}');
  await page.getByLabel("Input payload (JSON)").nth(1).fill('["analysis"]');
  await page
    .getByLabel("Expected output contract")
    .nth(0)
    .fill("A concise analysis.");
  await page
    .getByLabel("Expected output contract")
    .nth(1)
    .fill("A tested patch.");
  await page.getByRole("button", { name: "Add edge" }).click();
  await page.getByRole("button", { name: "Create immutable revision" }).click();

  await expect(
    page.getByRole("article", { name: "Workflow revision v1" }),
  ).toBeVisible();
  await expect(page.getByText("Created revision v1")).toBeVisible();
  await expect(page.getByText("v1 — active")).not.toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 — active")).toBeVisible();
  await page.reload();
  await expect(page.getByText("v1 — active")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Disable Workflow" }).click();
  await expect(page.getByText("Lifecycle status").locator("..")).toContainText(
    "disabled",
  );
  await expect(page.getByText("v1 — selected")).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate v1" })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 — active")).toBeVisible();
  await expect(page.getByText("Workflow revision v2")).not.toBeVisible();
});
