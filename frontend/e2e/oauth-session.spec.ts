import { test, expect } from "@playwright/test";

test("Google-style cross-site return retains login and still validates OAuth state", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("admin@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-test-only-2026!");
  await page.getByRole("button", { name: "Sign in to workspace" }).click();
  await expect(page.getByLabel("Switch workspace")).toBeVisible();

  const origin = new URL(page.url()).origin;
  const callback = `${origin}/api/v1/connections/google/callback?state=invalid-test-state&code=test-only-code`;
  // A different site is necessary: API test clients don't enforce SameSite.
  // This response is fulfilled locally; no Google or external account is used.
  await page.route("https://oauth.example.test/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<a href="${callback}">Return to Relay</a>`,
    }),
  );
  await page.goto("https://oauth.example.test/consent");
  const callbackResponse = page.waitForResponse((r) => r.url() === callback);
  await page.getByRole("link", { name: "Return to Relay" }).click();
  const response = await callbackResponse;
  expect(response.status()).toBe(400);
  expect(await response.json()).toEqual({
    detail: "Sign-in expired or invalid; start again from Connections",
  });

  expect((await page.request.get(`${origin}/api/v1/auth/me`)).status()).toBe(
    200,
  );
  // Allowing safe top-level GET navigation must not bypass CSRF on writes.
  expect(
    (await page.request.post(`${origin}/api/v1/auth/logout`)).status(),
  ).toBe(403);
  expect((await page.request.get(`${origin}/api/v1/auth/me`)).status()).toBe(
    200,
  );
});
