// Deterministic frame-stepping capture: seek the GSAP timeline per frame, screenshot with headless Chrome.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const [page_, outDir, poster] = process.argv.slice(2);
const FPS = 30, DUR = 15, POSTER_T = 4.9;
function findChrome() {
  const base = path.join(process.env.HOME, ".cache/puppeteer/chrome-headless-shell");
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  if (fs.existsSync(base)) {
    const v = fs.readdirSync(base).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).pop();
    const p = path.join(base, v, "chrome-headless-shell-mac-arm64", "chrome-headless-shell");
    if (fs.existsSync(p)) return p;
  }
  return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
}
(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ executablePath: findChrome(), args: ["--allow-file-access-from-files"] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => console.error("pageerror", e.message));
  await page.goto("file://" + path.resolve(page_));
  await page.evaluate(() => document.fonts.ready);
  const n = FPS * DUR;
  for (let i = 0; i < n; i++) {
    const t = i / FPS;
    await page.evaluate((t) => window.__seek(t), t);
    const f = path.join(outDir, "f" + String(i).padStart(4, "0") + ".png");
    await page.screenshot({ path: f });
    if (Math.abs(t - POSTER_T) < 1 / FPS / 2 + 1e-9) fs.copyFileSync(f, poster);
  }
  await browser.close();
  console.log("frames:", n);
})();
