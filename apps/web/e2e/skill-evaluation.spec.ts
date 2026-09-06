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

test("operator can deterministically grade supplied outputs for an exact revision", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const skillName = `E2E Evaluation Skill ${suffix}`;
  const skillKey = `e2e-evaluation-skill-${suffix}`;
  await page.goto(webUrl());
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await page.getByLabel("Key").fill(skillKey);
  await page.getByLabel("Display name").fill(skillName);
  await page.getByLabel("Description").fill("Deterministic evaluation E2E.");
  await page.getByRole("button", { name: "Create Skill", exact: true }).click();
  await expect(page.getByRole("heading", { name: skillName })).toBeVisible();

  const skillId = await detailValue(page, /^Skill ID$/);
  await page
    .getByLabel("Instructions")
    .fill("Use the exact evaluation revision.");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v1/);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 - active")).toBeVisible();
  const revisionId = await detailValue(page, /^Selected revision pointer$/);

  const suiteName = `Safety suite ${suffix}`;
  await page.getByLabel("Suite name").fill(suiteName);
  await page
    .getByLabel("Description", { exact: true })
    .last()
    .fill("Grades explicit outputs only.");
  const caseOne = page.getByRole("group", { name: "Evaluation case 1" });
  await caseOne.getByLabel("Case input").fill("Return SAFE");
  await caseOne.getByLabel("Expected exact value").fill("SAFE");
  await page.getByRole("button", { name: "Add case" }).click();
  const caseTwo = page.getByRole("group", { name: "Evaluation case 2" });
  await caseTwo.getByLabel("Case input").fill("Avoid DANGEROUS");
  await caseTwo.getByLabel("Grading kind").selectOption("contains_none");
  await caseTwo.getByLabel("Forbidden value 1").fill("DANGEROUS");
  await page.getByRole("button", { name: "Create evaluation suite" }).click();
  await expect(
    page.getByRole("heading", { name: "Selected evaluation suite" }),
  ).toBeVisible();
  await expect(page.getByText("Case 1").first()).toBeVisible();
  await expect(page.getByText("Case 2").first()).toBeVisible();

  await page.getByLabel("Target revision").selectOption(revisionId);
  await caseOne.getByLabel("Supplied output").fill("SAFE");
  await caseTwo.getByLabel("Supplied output").fill("normal response");

  const sideEffectPosts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") {
      const pathname = new URL(request.url()).pathname;
      if (!pathname.includes("/evaluation-suites/"))
        sideEffectPosts.push(pathname);
    }
  });
  await page.getByRole("button", { name: "Grade supplied outputs" }).click();
  await expect(page).toHaveURL(/\?view=skill-evaluation-run&id=/);
  const evaluationRunId = new URL(page.url()).searchParams.get("id");
  if (!evaluationRunId)
    throw new Error("grading did not navigate to an Evaluation Run");
  await expect(
    page.getByRole("heading", { name: "Evaluation Run Detail" }),
  ).toBeVisible();

  const runResponse = await page.request.get(
    `${apiUrl()}/v1/skills/evaluation-runs/${evaluationRunId}`,
  );
  expect(runResponse.ok()).toBe(true);
  const run = (await runResponse.json()) as {
    revision_id: string;
    proposal_id: string | null;
    target_content_sha256: string;
    runtime_fingerprint: string;
    aggregate_result: { case_count: number; passed: number; score: number };
    suite_snapshot: { cases: Array<{ id: string; position: number }> };
    case_results: Array<{ output_sha256: string }>;
  };
  expect(run.revision_id).toBe(revisionId);
  expect(run.proposal_id).toBeNull();
  expect(run.aggregate_result).toMatchObject({
    case_count: 2,
    passed: 2,
    score: 1,
  });
  expect(run.suite_snapshot.cases).toHaveLength(2);
  expect(run.suite_snapshot.cases.map((item) => item.position)).toEqual([1, 2]);
  expect(run.case_results).toHaveLength(2);
  expect(
    run.case_results.every((item) => item.output_sha256.length === 64),
  ).toBe(true);
  await expect(page.getByText(revisionId)).toBeVisible();
  await expect(page.getByText(run.target_content_sha256)).toBeVisible();
  await expect(page.getByText(run.runtime_fingerprint)).toBeVisible();
  await expect(
    page.getByText(run.suite_snapshot.cases[0]!.id, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(run.suite_snapshot.cases[1]!.id, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(run.case_results[0]!.output_sha256),
  ).toBeVisible();
  expect(sideEffectPosts).toEqual([]);

  const runUrl = page.url();
  await page.reload();
  await expect(page.getByText(revisionId)).toBeVisible();
  await expect(page.getByText(run.target_content_sha256)).toBeVisible();
  await expect(
    page.getByText(run.suite_snapshot.cases[0]!.id, { exact: true }),
  ).toBeVisible();

  await page.goto(`${webUrl()}/?view=skill&id=${skillId}`);
  await expect(page.getByRole("heading", { name: skillName })).toBeVisible();
  await page
    .getByLabel("Instructions")
    .fill("A later revision must not rewrite history.");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v2/);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v2" }).click();
  await expect(page.getByText("v2 - active")).toBeVisible();

  await page.goto(runUrl);
  await page.reload();
  await expect(page.getByText(revisionId)).toBeVisible();
  await expect(page.getByText(run.target_content_sha256)).toBeVisible();
  await expect(
    page.getByText(run.suite_snapshot.cases[0]!.id, { exact: true }),
  ).toBeVisible();
});
