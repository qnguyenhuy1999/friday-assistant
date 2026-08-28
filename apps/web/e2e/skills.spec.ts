import { expect, test } from "@playwright/test";

test("an operator manages immutable Skill revisions without re-enable semantics", async ({
  page,
}) => {
  const url = process.env.FRIDAY_E2E_WEB_URL;
  if (!url) throw new Error("FRIDAY_E2E_WEB_URL was not set by global setup");

  await page.goto(url);
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await page.getByLabel("Key").fill("e2e-operator-skill");
  await page.getByLabel("Display name").fill("E2E Operator Skill");
  await page
    .getByLabel("Description")
    .fill("A durable browser proof for Skill operator semantics.");
  await page.getByRole("button", { name: "Create Skill", exact: true }).click();

  await expect(
    page.getByRole("heading", { name: "E2E Operator Skill" }),
  ).toBeVisible();
  await page
    .getByLabel("Instructions")
    .fill("Use the persisted operator instructions exactly.");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v1/);
  await expect(page.getByText("v1 - active")).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "v1", exact: true }),
  ).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v1" }).click();
  await expect(page.getByText("v1 - active")).toBeVisible();

  await page
    .getByLabel("Instructions")
    .fill("Keep the second persisted instruction set separate.");
  await page.getByRole("button", { name: "Create immutable revision" }).click();
  await expect(page.getByRole("status")).toHaveText(/Created revision v2/);
  await expect(page.getByText("v1 - active")).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate v2" })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Activate v2" }).click();
  await expect(page.getByText("v2 - active")).toBeVisible();
  await expect(
    page.getByText("Historical revision - rollback required."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Activate v1" }),
  ).not.toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Disable Skill" }).click();
  await expect(page.getByText("Lifecycle status").locator("..")).toContainText(
    "disabled",
  );
  await expect(page.getByText("v2 - selected, Skill disabled")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Enable Skill|Re-enable Skill/ }),
  ).not.toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Archive Skill" }).click();
  await expect(page.getByText(/archived and read-only/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Create immutable revision" }),
  ).not.toBeVisible();
  await expect(page.getByText("v2 - selected, Skill archived")).toBeVisible();
  await expect(
    page.getByText("Keep the second persisted instruction set separate."),
  ).toBeVisible();
});
