import { expect, test } from "@playwright/test";

/**
 * Real-browser smoke for the igv.js guide viewer (closes the long-standing "manual browser
 * eyeball" gap: happy-dom can't run igv's canvas, so the vitest component tests mock igv).
 *
 * Drives the standalone /showcase.html page, which renders the REAL IgvGuideViewer with mock
 * data and NO backend/network — the guide viewer uses the submitted sequence as its own
 * inline-FASTA reference, so this is fully hermetic. We verify igv actually paints a track
 * canvas in a browser, and that the honesty posture ("not a genome build") is on the page.
 */
test.describe("igv.js genome browser — real-browser smoke", () => {
  test("loads the guide viewer and paints a real track canvas", async ({ page }) => {
    await page.goto("/showcase.html");

    // The honest "this is the submitted locus, not a genome build" note ships before any load.
    await expect(page.getByText(/not a genome build/i).first()).toBeVisible();

    // Nothing has painted a canvas yet (the other cards are SVG/DOM only) — a clean baseline.
    expect(await page.locator("canvas").count()).toBe(0);

    const load = page.getByRole("button", { name: /load genome browser/i });
    await expect(load).toBeVisible();
    await load.click();

    // igv.createBrowser resolved (state -> "ready"), NOT an error/install fallback.
    await expect(page.getByText(/^Loaded$/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/igv\.js failed to render/i)).toHaveCount(0);
    await expect(page.getByText(/viewer not installed/i)).toHaveCount(0);

    // The thing happy-dom can't do: igv painted at least one real <canvas> track.
    await expect
      .poll(async () => page.locator("canvas").count(), { timeout: 30_000 })
      .toBeGreaterThan(0);
  });
});
