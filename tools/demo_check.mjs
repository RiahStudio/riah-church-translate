// Opens the public demo (riahstudio.com/churchtranslate) in the three browser engines people use on
// Macs and PCs (Chromium = Chrome and Edge, WebKit = Safari, Firefox), at laptop and phone sizes,
// and checks that subtitles play, the language buttons work, Full screen opens and closes, and
// nothing errors or scrolls sideways. Saves a picture of each into demo-check/.
//   node tools/demo_check.mjs [url]      (CI: .github/workflows/demo-check.yml)
import { chromium, firefox, webkit } from "playwright";
import { mkdirSync } from "node:fs";

const url = process.argv[2] || "https://riahstudio.com/churchtranslate";
const THAI = /[฀-๿]/, HAN = /[一-鿿]/;
const sizes = { laptop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };
mkdirSync("demo-check", { recursive: true });
let failed = 0;

for (const [name, engine] of Object.entries({ chromium, firefox, webkit })) {
  const browser = await engine.launch();
  for (const [size, viewport] of Object.entries(sizes)) {
    const tag = `${name}-${size}`;
    const page = await browser.newPage({ viewport, isMobile: size === "phone" && name !== "firefox" });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    // The site-wide report-only security header makes WebKit log notices about itself; they don't affect the page.
    const siteNotice = /report-only|report-uri|report-to/i;
    page.on("console", (m) => { if (m.type() === "error" && !siteNotice.test(m.text())) errors.push(m.text()); });
    const problems = [];
    try {
      await page.goto(url, { waitUntil: "load", timeout: 60000 });
      const screen = page.frameLocator("#screen");
      const rows = screen.locator("#rows");
      await rows.filter({ hasText: THAI }).waitFor({ timeout: 45000 });
      await page.click('[data-lang="zh"]');
      await page.waitForFunction(() => /[一-鿿]/.test(
        document.getElementById("screen").contentDocument.getElementById("rows").textContent), null, { timeout: 45000 });
      const text = await rows.textContent();
      if (!THAI.test(text)) problems.push("no Thai on the screen");
      if (!HAN.test(text)) problems.push("no Chinese after turning it on");
      const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (wide > 1) problems.push(`page scrolls sideways by ${wide}px`);
      await page.screenshot({ path: `demo-check/${tag}.png` });
      await page.click("#full");
      await page.waitForTimeout(800);
      const full = await page.evaluate(() => Boolean(document.fullscreenElement) ||
        document.getElementById("player").classList.contains("is-filled"));
      if (!full) problems.push("Full screen did nothing");
      await page.screenshot({ path: `demo-check/${tag}-full.png` });
      await page.keyboard.press("Escape");
      if (await page.evaluate(() => document.getElementById("player").classList.contains("is-filled"))) {
        await page.click("#exit");
      }
    } catch (e) {
      problems.push(String(e).split("\n")[0]);
      await page.screenshot({ path: `demo-check/${tag}-failed.png` }).catch(() => {});
    }
    problems.push(...errors.map((e) => `console: ${e}`));
    console.log(`${problems.length ? "FAIL" : "ok  "} ${tag}${problems.length ? ": " + problems.join("; ") : ""}`);
    failed += problems.length ? 1 : 0;
    await page.close();
  }
  await browser.close();
}
console.log(failed ? `${failed} failed` : "PASS");
process.exit(failed ? 1 : 0);
