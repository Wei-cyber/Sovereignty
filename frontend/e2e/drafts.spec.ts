import { test, expect } from "@playwright/test";
test("edit an email, explicitly save, and reconcile the same draft operation", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-test-only-2026!");
  await page.getByRole("button", { name: "Sign in to workspace" }).click();
  await expect(page.getByLabel("Switch workspace")).toBeVisible();
  const session = await (await page.request.get("/api/v1/auth/me")).json();
  const spaces = await (await page.request.get("/api/v1/workspaces")).json();
  const workspace = spaces[0].id;
  const agent = await (
    await page.request.post(`/api/v1/workspaces/${workspace}/agents`, {
      headers: { "X-CSRF-Token": session.csrf_token },
      data: {
        name: "Draft test agent",
        instructions: "Prepare editable drafts",
        tools: [],
      },
    })
  ).json();
  await page.route("**/connections", (route) =>
    route.fulfill({
      json: {
        configured: true,
        picker_configured: false,
        callback_url: "test",
        allowed_tools: ["gmail_save_draft"],
        connections: [
          {
            id: "test",
            provider: "gmail",
            email: "test@example.test",
            active: true,
            drafts_enabled: true,
            selected_files: [],
          },
        ],
      },
    }),
  );
  const requests: any[] = [];
  await page.route("**/gmail/drafts", (route) => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({
      json:
        requests.length === 1
          ? { status: "uncertain", message: "Check the same save again" }
          : { status: "completed", draft_id: "test-draft" },
    });
  });
  await page.reload();
  await page.getByLabel("Select assistant").selectOption(agent.id);
  await page.getByLabel("Ask your workspace").fill("Draft a test email");
  await page.getByRole("button", { name: "Send question" }).click();
  await expect(
    page.getByRole("heading", { name: "Review email draft" }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Subject", exact: true })
    .fill("Edited subject");
  await page
    .getByRole("textbox", { name: "Message", exact: true })
    .fill("Edited test body");
  expect(requests).toHaveLength(0);
  await page
    .getByRole("button", { name: "Save draft to Gmail", exact: true })
    .click();
  await expect(page.getByText("Check the same save again")).toBeVisible();
  await page
    .getByRole("button", { name: "Check draft save", exact: true })
    .click();
  await expect(
    page.getByText("Saved to Gmail Drafts. Nothing was sent."),
  ).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
  expect(requests[0].draft.subject).toBe("Edited subject");
  expect(requests[0].draft.body).toBe("Edited test body");
});
