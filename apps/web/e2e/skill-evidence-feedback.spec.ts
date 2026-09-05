import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

test.setTimeout(120_000);

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
      .first()
      .textContent()) ?? ""
  ).trim();
}

async function createActiveSkill(
  page: Page,
  displayName: string,
  key: string,
  instructions: string,
): Promise<{ id: string; key: string; revisionId: string }> {
  await page.goto(webUrl());
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await page.getByLabel("Key").fill(key);
  await page.getByLabel("Display name").fill(displayName);
  await page
    .getByLabel("Description")
    .fill("A durable Skill evidence and feedback E2E proof.");
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
    key,
    revisionId: await detailValue(page, /^Selected revision pointer$/),
  };
}

async function waitForTerminalRun(
  page: Page,
  runId: string,
): Promise<Record<string, unknown>> {
  let terminalRun: Record<string, unknown> | null = null;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${apiUrl()}/v1/runs/${runId}`);
        if (!response.ok()) return null;
        const run = (await response.json()) as Record<string, unknown>;
        if (
          run.status !== "succeeded" &&
          run.status !== "failed" &&
          run.status !== "cancelled"
        )
          return null;
        terminalRun = run;
        return run;
      },
      { timeout: 30_000 },
    )
    .not.toBeNull();
  if (!terminalRun) throw new Error("Run did not reach a terminal state");
  return terminalRun;
}

async function waitForUsageRecord(
  page: Page,
  skillId: string,
  runId: string,
): Promise<Record<string, unknown>> {
  let usageRecord: Record<string, unknown> | null = null;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${apiUrl()}/v1/skills/${skillId}/usage`,
        );
        if (!response.ok()) return null;
        const records = (await response.json()) as Array<
          Record<string, unknown>
        >;
        usageRecord = records.find((record) => record.run_id === runId) ?? null;
        return usageRecord;
      },
      { timeout: 30_000 },
    )
    .not.toBeNull();
  if (!usageRecord) throw new Error("Usage evidence was not materialized");
  return usageRecord;
}

test("operator can inspect frozen Skill evidence and persist Run feedback", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const skill = await createActiveSkill(
    page,
    `E2E Evidence Skill ${suffix}`,
    `e2e-evidence-skill-${suffix}`,
    "Use this exact frozen instruction for the evidence proof.",
  );

  const taskTitle = `Skill evidence feedback E2E ${suffix}`;
  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page.getByLabel("Title").fill(taskTitle);
  await page.getByRole("button", { name: "Create task" }).click();
  const taskRow = page.getByRole("listitem").filter({ hasText: taskTitle });
  await expect(taskRow).toBeVisible();
  await taskRow.getByRole("button", { name: taskTitle, exact: true }).click();
  await expect(page.getByRole("heading", { name: taskTitle })).toBeVisible();

  const taskId = await detailValue(page, /^Task ID$/);
  await page.getByLabel("Skill", { exact: true }).selectOption(skill.id);
  await page.getByRole("button", { name: "Add selected Skill" }).click();
  await page.getByRole("button", { name: "Save Skill composition" }).click();
  await expect(page.getByRole("button", { name: "Start Run" })).toBeEnabled();
  await page.getByRole("button", { name: "Start Run", exact: true }).click();
  await expect(page).toHaveURL(/\?view=run&id=/);

  const runId = new URL(page.url()).searchParams.get("id");
  if (!runId) throw new Error("Task Detail did not navigate to a Run");

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
      items: [
        {
          skill_id: skill.id,
          revision_id: skill.revisionId,
          position: 1,
        },
      ],
    });

  const terminalRun = await waitForTerminalRun(page, runId);
  const usageRecord = await waitForUsageRecord(page, skill.id, runId);

  await page.goto(`${webUrl()}/?view=skill&id=${skill.id}`);
  const evidence = page.getByRole("article", {
    name: `Usage evidence for Run ${runId}`,
  });
  await expect(evidence).toBeVisible();
  await expect(evidence).toContainText(runId);
  await expect(evidence).toContainText(taskId);
  await expect(evidence).toContainText(skill.revisionId);
  await expect(evidence).toContainText(String(usageRecord.execution_id));
  await expect(evidence).toContainText(String(usageRecord.attempt_number));
  await expect(evidence).toContainText(String(usageRecord.outcome));
  await expect(evidence).toContainText(String(terminalRun.status));

  await evidence.getByRole("button", { name: "View Run" }).click();
  await expect(page).toHaveURL(`${webUrl()}/?view=run&id=${runId}`);
  const feedbackForm = page.getByRole("form", {
    name: `Feedback for Skill ${skill.key}`,
  });
  await expect(feedbackForm).toBeVisible();
  await feedbackForm.getByLabel("Rating").selectOption("helpful");
  await feedbackForm.getByLabel("Created by").fill("e2e-operator");
  await feedbackForm.getByLabel("Note").fill("Observed useful behavior.");
  await feedbackForm.getByRole("button", { name: "Submit feedback" }).click();

  const feedback = page.getByRole("article", { name: /Feedback record/ });
  await expect(feedback).toContainText(runId);
  await expect(feedback).toContainText(skill.id);
  await expect(feedback).toContainText(skill.revisionId);
  await expect(feedback).toContainText("helpful");
  await expect(feedback).toContainText("e2e-operator");
  await expect(feedback).toContainText("Observed useful behavior.");

  await page.reload();
  const persistedFeedback = page.getByRole("article", {
    name: /Feedback record/,
  });
  await expect(persistedFeedback).toContainText(runId);
  await expect(persistedFeedback).toContainText(skill.id);
  await expect(persistedFeedback).toContainText(skill.revisionId);
  await expect(persistedFeedback).toContainText("helpful");
  await expect(persistedFeedback).toContainText("e2e-operator");
  await expect(persistedFeedback).toContainText("Observed useful behavior.");
});
