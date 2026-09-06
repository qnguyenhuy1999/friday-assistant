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

test("operator can configure and request safe improvement processing", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const skillName = `E2E Improvement Skill ${suffix}`;
  const skillKey = `e2e-improvement-skill-${suffix}`;
  await page.goto(webUrl());
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await page.getByLabel("Key").fill(skillKey);
  await page.getByLabel("Display name").fill(skillName);
  await page.getByLabel("Description").fill("Safe improvement policy E2E.");
  await page.getByRole("button", { name: "Create Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: skillName })).toBeVisible();

  const skillId = await detailValue(page, /^Skill ID$/);
  await page.getByLabel("Instructions").fill("Use the exact base instruction.");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v1/);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 - active")).toBeVisible();

  await page.getByLabel("Suite name").fill(`Improvement suite ${suffix}`);
  await page
    .getByLabel("Description", { exact: true })
    .last()
    .fill("Evaluates safe candidate processing.");
  const evaluationCase = page.getByRole("group", { name: "Evaluation case 1" });
  await evaluationCase.getByLabel("Case input").fill("Return SAFE");
  await evaluationCase.getByLabel("Expected exact value").fill("SAFE");
  await page.getByRole("button", { name: "Create evaluation suite" }).click();
  await expect(
    page.getByRole("heading", { name: "Selected evaluation suite" }),
  ).toBeVisible();
  const suiteId = await detailValue(page, /^Suite ID$/);

  await page
    .getByLabel("Evaluation suite", { exact: true })
    .selectOption(suiteId);
  await page.getByRole("button", { name: "Save improvement policy" }).click();
  await expect(page.getByText("Improvement policy saved.")).toBeVisible();
  await expect(page.getByText("Maximum open proposals").last()).toBeVisible();
  await expect(
    page.getByText("brain-candidate-generator-v2", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("comparison-v1", { exact: true }).first(),
  ).toBeVisible();

  const persistedPolicy = await page.request.get(
    `${apiUrl()}/v1/skills/${skillId}/improvement-policy`,
  );
  await expect(persistedPolicy).toBeOK();
  const persistedPolicyBody = (await persistedPolicy.json()) as {
    evaluation_suite_id: string;
  };
  expect(persistedPolicyBody.evaluation_suite_id).toBe(suiteId);

  await page.reload();
  await expect(page.getByRole("heading", { name: skillName })).toBeVisible();
  await expect(
    page.getByLabel("Evaluation suite", { exact: true }),
  ).toHaveValue(suiteId);
  await expect(page.getByText("Improvement policy saved.")).not.toBeVisible();

  const postPaths: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST")
      postPaths.push(new URL(request.url()).pathname);
  });
  await page
    .getByRole("button", { name: "Request improvement processing" })
    .click();
  await expect(
    page.getByText(
      "No improvement processing was due under the current persisted policy.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("Skill is healthy", { exact: false }),
  ).not.toBeVisible();
  await expect(page.getByText("No improvement proposals yet.")).toBeVisible();
  expect(postPaths).toEqual([
    `/v1/skills/${skillId}/improvement-policy/run-now`,
  ]);

  const proposals = await page.request.get(
    `${apiUrl()}/v1/skills/${skillId}/improvement-proposals`,
  );
  await expect(proposals).toBeOK();
  expect(await proposals.json()).toEqual([]);
});
