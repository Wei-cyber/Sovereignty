import { test, expect } from "@playwright/test";

test("prepare human examples, resume a draft, check and activate the grader", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-test-only-2026!");
  await page.getByRole("button", { name: "Sign in to workspace" }).click();
  await expect(page.getByLabel("Switch workspace")).toBeVisible();
  const session = await (await page.request.get("/api/v1/auth/me")).json();
  const created = await page.request.post("/api/v1/workspaces", {
    data: { name: `Grader UI ${Date.now()}` },
    headers: { "X-CSRF-Token": session.csrf_token },
  });
  expect(created.status()).toBe(201);
  const workspace = (await created.json()).id;
  await page.reload();
  await page.getByLabel("Switch workspace").selectOption(workspace);
  await page.getByRole("button", { name: "Knowledge", exact: true }).click();
  const reference = "Employees receive 25 days of annual leave.";
  await page.getByLabel("Upload knowledge documents").setInputFiles({
    name: "Grader policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(`# Leave policy\n${reference}`),
  });
  await expect(
    page
      .getByRole("row")
      .filter({ hasText: "Grader policy.md" })
      .getByText("ready", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Evaluations", exact: true })
    .click();
  await page.getByRole("button", { name: "Grader check", exact: true }).click();
  await page
    .getByRole("button", { name: "Prepare examples", exact: true })
    .click();
  await page.getByLabel("Check name").fill("Leave policy grading");
  await expect(page.getByLabel("Your correctness rating")).toHaveValue("");
  await page
    .getByLabel("Grader question 1")
    .fill("How much annual leave do employees receive, example 1?");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await page.reload();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Evaluations", exact: true })
    .click();
  await page.getByRole("button", { name: "Grader check", exact: true }).click();
  await page
    .getByRole("button", { name: "Edit examples", exact: true })
    .click();
  await expect(page.getByLabel("Grader question 1")).toHaveValue(
    "How much annual leave do employees receive, example 1?",
  );
  for (let i = 0; i < 10; i++) {
    if (i) {
      await page
        .getByRole("button", { name: "Add example", exact: true })
        .click();
      await page
        .getByRole("dialog")
        .locator("summary")
        .filter({ hasText: `Example ${i + 1} ·` })
        .click();
    }
    const example = page.getByRole("dialog").locator("details").nth(i);
    await example
      .getByLabel(`Grader question ${i + 1}`, { exact: true })
      .fill(`How much annual leave do employees receive, example ${i + 1}?`);
    await example
      .getByLabel("Choose a document")
      .selectOption({ label: "Grader policy.md · version 1" });
    await example.getByRole("checkbox").check();
    await example.getByLabel("Verified correct answer").fill(reference);
    await example
      .getByLabel("Sample answer to grade")
      .fill(i < 5 ? "Penguins inhabit Antarctica." : reference);
    await example
      .getByLabel("Your correctness rating")
      .selectOption(i < 5 ? "0" : "1");
    await example
      .getByLabel("Your evidence-support rating")
      .selectOption(i < 5 ? "0" : "1");
    if (i === 0)
      await page.screenshot({
        path: testInfo.outputPath("grader-example-form.png"),
        fullPage: true,
      });
  }
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await page
    .getByRole("button", { name: "Review and check grader", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Check grader", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel("I reviewed the evidence and entered these ratings myself.")
    .check();
  await page
    .getByLabel("Review notes")
    .fill(
      "Automated UI fixture in an isolated test workspace; this is not a real human review.",
    );
  await page.getByRole("button", { name: "Check grader", exact: true }).click();
  await expect(
    page.getByText("Grader agrees closely enough", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Use for this workspace", exact: true })
    .click();
  await expect(
    page.getByText("Using Leave policy grading", { exact: true }),
  ).toBeVisible();
  await page.getByText("Compare human and AI scores", { exact: true }).click();
  await page.screenshot({
    path: testInfo.outputPath("grader-results.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: testInfo.outputPath("grader-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
