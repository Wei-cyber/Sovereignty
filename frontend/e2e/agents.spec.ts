import { test, expect } from "@playwright/test";

test("personal agent editing, preview, sharing request and unavailable connections", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-test-only-2026!");
  await page.getByRole("button", { name: "Sign in to workspace" }).click();
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByRole("button", { name: "Create agent", exact: true }).click();
  const name = `Personal research ${Date.now()}`;
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page
    .getByLabel("Description", { exact: true })
    .fill("My private research helper");
  await page
    .getByLabel("Conversation starters")
    .fill("How long is onboarding?");
  await page.getByRole("button", { name: "Save agent", exact: true }).click();
  const card = page.locator("article").filter({ hasText: name });
  await expect(card.getByText("Not reviewed · Private")).toBeVisible();
  await card.getByRole("button", { name: "Preview", exact: true }).click();
  await page
    .getByLabel("Try a question")
    .fill("What is the rainfall on Jupiter?");
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(
    page.locator(".agent-preview").getByText("completed", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await card.getByRole("button", { name: "Request sharing" }).click();
  await expect(
    page.getByText(
      "Submitted for administrator review in Workflows. Your private agent stays available.",
    ),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("agents.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  await page
    .getByLabel("Select assistant")
    .selectOption({ label: name + " · Not reviewed" });
  await expect(
    page.getByRole("button", { name: "How long is onboarding?", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Connections", exact: true }).click();
  await expect(
    page.getByText("Google connections are unavailable"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Connect account" }).first(),
  ).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "Workspace tool policy" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("connections.png"),
    fullPage: true,
  });
});
