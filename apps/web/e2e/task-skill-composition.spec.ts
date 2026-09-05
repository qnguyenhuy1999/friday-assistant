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

async function createActiveSkill(
  page: Page,
  displayName: string,
  key: string,
  instructions: string,
): Promise<{ id: string; revisionId: string }> {
  await page.goto(webUrl());
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await page.getByLabel("Key").fill(key);
  await page.getByLabel("Display name").fill(displayName);
  await page
    .getByLabel("Description")
    .fill("A Task Skill composition E2E Skill.");
  await page.getByRole("button", { name: "Create Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: displayName })).toBeVisible();

  const id = await detailValue(page, /^Skill ID$/);
  await page.getByLabel("Instructions").fill(instructions);
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v1/);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 - active")).toBeVisible();
  return {
    id,
    revisionId: await detailValue(page, /^Selected revision pointer$/),
  };
}

async function waitForFrozenSkills(
  page: Page,
  runId: string,
  expected: Array<{ skillId: string; revisionId: string }>,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${apiUrl()}/v1/runs/${runId}/skills`,
        );
        if (!response.ok()) return null;
        return response.json();
      },
      { timeout: 30_000 },
    )
    .toMatchObject({
      resolved: true,
      items: expected.map(({ skillId, revisionId }, position) => ({
        skill_id: skillId,
        revision_id: revisionId,
        position: position + 1,
      })),
    });
}

test("keeps frozen Run Skill provenance after Task composition changes", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const skillAName = `E2E Code Review ${suffix}`;
  const skillBName = `E2E Security Review ${suffix}`;
  const skillA = await createActiveSkill(
    page,
    skillAName,
    `e2e-review-code-${suffix}`,
    "First frozen Skill instruction set.",
  );
  const skillB = await createActiveSkill(
    page,
    skillBName,
    `e2e-review-security-${suffix}`,
    "Second frozen Skill instruction set.",
  );

  const taskTitle = `Skill composition E2E ${suffix}`;
  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page.getByLabel("Title").fill(taskTitle);
  await page.getByRole("button", { name: "Create task" }).click();
  const taskRow = page.getByRole("listitem").filter({ hasText: taskTitle });
  await expect(taskRow).toBeVisible();
  await taskRow.getByRole("button", { name: taskTitle, exact: true }).click();
  await expect(page.getByRole("heading", { name: taskTitle })).toBeVisible();

  const skillSelect = page.getByLabel("Skill", { exact: true });
  await skillSelect.selectOption(skillA.id);
  await page.getByRole("button", { name: "Add selected Skill" }).click();
  await skillSelect.selectOption(skillB.id);
  await page.getByRole("button", { name: "Add selected Skill" }).click();
  await page.getByRole("button", { name: "Save Skill composition" }).click();
  await expect(page.getByRole("button", { name: "Start Run" })).toBeEnabled();

  await page.getByRole("button", { name: "Start Run", exact: true }).click();
  await expect(page).toHaveURL(/\?view=run&id=/);
  const runId = new URL(page.url()).searchParams.get("id");
  if (!runId) throw new Error("Task Detail did not navigate to a Run");

  await waitForFrozenSkills(page, runId, [
    { skillId: skillA.id, revisionId: skillA.revisionId },
    { skillId: skillB.id, revisionId: skillB.revisionId },
  ]);

  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page
    .getByRole("listitem")
    .filter({ hasText: taskTitle })
    .getByRole("button", { name: taskTitle, exact: true })
    .click();
  await expect(page.getByRole("heading", { name: taskTitle })).toBeVisible();
  await page.getByRole("button", { name: `Remove ${skillAName}` }).click();
  await page.getByRole("button", { name: "Save Skill composition" }).click();
  await expect(
    page
      .getByRole("list", { name: "Task Skill composition" })
      .getByRole("listitem"),
  ).toHaveCount(1);

  await page.goto(`${webUrl()}/?view=run&id=${runId}`);
  await expect(page.getByText("Frozen Skill provenance")).toBeVisible();
  const firstFrozen = page.getByRole("article", {
    name: `Frozen Skill 1: e2e-review-code-${suffix}`,
  });
  const secondFrozen = page.getByRole("article", {
    name: `Frozen Skill 2: e2e-review-security-${suffix}`,
  });
  await expect(firstFrozen).toContainText(skillA.id);
  await expect(firstFrozen).toContainText(skillA.revisionId);
  await expect(secondFrozen).toContainText(skillB.id);
  await expect(secondFrozen).toContainText(skillB.revisionId);
});
