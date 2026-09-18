import { test, expect } from "@playwright/test";

test("upload, build, evaluate, publish, ask, cite, and give feedback", async ({
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
  await expect(
    page.getByRole("heading", { name: /What can we help/ }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("assistant-desktop.png"),
    fullPage: true,
  });
  const suffix = Date.now().toString();
  const session = await (await page.request.get("/api/v1/auth/me")).json();
  const created = await page.request.post("/api/v1/workspaces", {
    data: { name: `Validation ${suffix}` },
    headers: { "X-CSRF-Token": session.csrf_token },
  });
  expect(created.status()).toBe(201);
  const workspace = (await created.json()).id;
  await page.reload();
  await page.getByLabel("Switch workspace").selectOption(workspace);
  const documentName = `browser-handbook-${suffix}.md`;
  await page.getByRole("button", { name: "Knowledge", exact: true }).click();
  await page.getByLabel("Upload knowledge documents").setInputFiles({
    name: documentName,
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# Browser test policy\nThe browser test team receives 29 vacation days per calendar year.",
    ),
  });
  const row = page.getByRole("row").filter({ hasText: documentName });
  await expect(row.getByText("ready", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: `View ${documentName}`, exact: true })
    .click();
  await expect(
    page.getByRole("dialog").getByText(/29 vacation days/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  const docs = await (
    await page.request.get(`/api/v1/workspaces/${workspace}/documents`)
  ).json();
  const doc = docs.find((d: { name: string }) => d.name === documentName);
  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await page
    .getByRole("button", { name: "Create workflow", exact: true })
    .click();
  const workflowName = `Browser assistant ${suffix}`;
  await page.getByLabel("Workflow name").fill(workflowName);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Create workflow", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: workflowName, exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Graph connections are valid")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("workflow-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await page
    .getByLabel("Test question")
    .fill("How many vacation days does the browser test team receive?");
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText(/receives 29 vacation days/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Evaluate", exact: true }).click();
  await page.getByRole("button", { name: "Create suite", exact: true }).click();
  await page.getByLabel("Suite name").fill(`Browser benchmark ${suffix}`);
  await page
    .getByLabel("Question 1", { exact: true })
    .fill("How many vacation days does the browser test team receive?");
  await page
    .getByLabel("Expected answer 1")
    .fill("The browser test team receives 29 vacation days per calendar year.");
  await page
    .getByRole("dialog")
    .getByRole("checkbox", { name: new RegExp(documentName) })
    .check();
  await page.getByRole("button", { name: "Add question", exact: true }).click();
  await page
    .getByLabel("Question 2", { exact: true })
    .fill("What is the rainfall on Jupiter?");
  await page.getByLabel("Expected behavior 2").selectOption("decline");
  await page.screenshot({
    path: testInfo.outputPath("suite-form.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Save suite", exact: true }).click();
  await page
    .getByRole("button", { name: "Review examples", exact: true })
    .click();
  await page
    .getByLabel("I have reviewed every example and its expected evidence.")
    .check();
  await page
    .getByLabel("Review notes")
    .fill(
      "Automated UI test of the review control; this is not a real human review.",
    );
  await page.getByRole("button", { name: "Record human review" }).click();
  await page
    .getByRole("button", { name: "Run evaluation", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Publish this version", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Publish this version", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("published");
  await page.screenshot({
    path: testInfo.outputPath("evaluations-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  await page
    .getByLabel("Select assistant")
    .selectOption({ label: workflowName });
  await page
    .getByLabel("Ask your workspace")
    .fill("How many vacation days does the browser test team receive?");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(page.locator(".markdown")).toContainText("29 vacation days");
  await page.locator(".source-chip").first().click();
  await expect(page.getByRole("dialog")).toContainText("29 vacation days");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .getByRole("button", { name: "Helpful answer", exact: true })
    .click();
  await page
    .getByLabel("What worked, or what should improve?")
    .fill("Browser test confirms a grounded answer and visible source.");
  await page
    .getByRole("button", { name: "Save feedback", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Feedback saved");
  await page.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "How many vacation days does the browser test team receive?",
  );
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.screenshot({
    path: testInfo.outputPath("answer-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeVisible();
  await expect(page.locator(".sidebar")).toBeHidden();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.getByLabel("Ask your workspace")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("answer-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
