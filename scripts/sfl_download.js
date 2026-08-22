// Usage: node sfl_download.js <url> <work_dir>
// Output: {"ok": true, "redirect_to": "https://sfile.mobi/..."} -- kalau ketemu
//         link sfile.mobi/sfile.co di ujung alur (diserahkan ke handler sfile)
//     atau {"ok": true, "direct_url": "..."} -- kalau ketemu file langsung
//     atau {"ok": false, "error": "..."}
//
// sfl.gl itu gerbang iklan bertingkat: scroll ke bawah -> klik tombol -> tunggu
// countdown timer (~15-25 detik) -> kadang buka tab baru -> ulangi lagi 2x atau
// lebih -- sampai akhirnya nyampe ke tujuan asli (biasanya link sfile.mobi).
// Makanya script ini nggak nyoba nangkep file sendiri, cukup "nembus" gerbang
// iklannya sampai ketemu URL tujuan akhir yang sudah kita punya handler-nya.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

function log(...args) {
  console.error("[debug]", ...args);
}

const FINAL_DESTINATION_DOMAINS = ["sfile.mobi", "sfile.co"];

const BUTTON_SELECTOR = [
  'a:has-text("Download")', 'button:has-text("Download")',
  'a:has-text("Get Link")', 'button:has-text("Get Link")',
  'a:has-text("Continue")', 'button:has-text("Continue")',
  'a:has-text("Get File")', 'button:has-text("Get File")',
  'a:has-text("Skip Ad")', 'button:has-text("Skip Ad")',
  'a:has-text("Next")', 'button:has-text("Next")',
  'a:has-text("Proceed")', 'button:has-text("Proceed")',
  '#download', '.download-btn', '[id*="download" i]', '[id*="continue" i]',
].join(", ");

function isFinalDestination(url) {
  return FINAL_DESTINATION_DOMAINS.some((d) => url.includes(d));
}

async function scrollAndClick(page, label) {
  // banyak situs "gerbang iklan" sengaja naruh tombol di bawah, biar user
  // kepaksa scroll dulu (dan kelewat lebih banyak iklan di sepanjang jalan)
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
  await new Promise((r) => setTimeout(r, 1500));

  const button = page.locator(BUTTON_SELECTOR).first();
  const isVisible = await button.isVisible({ timeout: 5000 }).catch(() => false);
  if (!isVisible) {
    log(`[${label}] tombol nggak ketemu setelah scroll`);
    return false;
  }
  log(`[${label}] scroll selesai, klik tombol`);
  await button.click({ timeout: 8000 }).catch((e) => log(`[${label}] klik gagal:`, e.message));
  return true;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node sfl_download.js <url> <work_dir>" }));
    process.exit(1);
  }
  if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });

  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
    });

    const context = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    });
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });

    let finalUrl = null;
    const pages = [];

    function trackPage(page, label) {
      pages.push({ page, label, closed: false });
      page.on("close", () => {
        const entry = pages.find((p) => p.page === page);
        if (entry) entry.closed = true;
      });
    }

    const mainPage = await context.newPage();
    trackPage(mainPage, "main");

    context.on("page", (popup) => {
      const label = `popup${pages.length}`;
      log(`tab baru terbuka [${label}]`);
      trackPage(popup, label);
    });

    log("navigasi ke:", url);
    await mainPage.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => log("goto error:", e.message));

    if (isFinalDestination(mainPage.url())) {
      finalUrl = mainPage.url();
    }

    // DEBUG: dibatasi 2 putaran dulu (biasanya 6) biar screenshot-nya fokus
    // ke titik awal proses -- gampang dilacak kalau ternyata nyasar dari awal
    for (let round = 1; round <= 2 && !finalUrl; round++) {
      // "tab aktif" = tab paling baru yang masih kebuka
      const active = [...pages].reverse().find((p) => !p.closed && !p.page.isClosed());
      if (!active) {
        log("nggak ada tab aktif tersisa, berhenti");
        break;
      }

      log(`=== putaran ke-${round}, tab aktif: [${active.label}], url: ${active.page.url()} ===`);

      if (isFinalDestination(active.page.url())) {
        finalUrl = active.page.url();
        break;
      }

      await active.page.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});

      // screenshot SEBELUM klik, biar kita bisa lihat persis kondisi halaman
      // di titik ini kalau ternyata proses nyasar setelah klik
      const preClickShot = path.join(workDir, `debug_round${round}_before_click.png`);
      await active.page.screenshot({ path: preClickShot }).catch(() => {});
      log(`[${active.label}] screenshot sebelum klik:`, preClickShot);

      await scrollAndClick(active.page, active.label);

      // tunggu countdown timer (situs ini kepake ~15-25 detik) -- cek tiap detik
      // siapa tau finalUrl ketemu lebih cepat (via navigasi tab aktif ATAU tab baru)
      for (let i = 0; i < 28 && !finalUrl; i++) {
        await new Promise((r) => setTimeout(r, 1000));

        // cek semua tab yang masih kebuka, siapa tau salah satunya udah nyampe finalUrl
        for (const p of pages) {
          if (p.closed || p.page.isClosed()) continue;
          const u = p.page.url();
          if (isFinalDestination(u)) {
            finalUrl = u;
            log(`finalUrl ketemu di [${p.label}]:`, u);
            break;
          }
        }
      }
    }

    if (!finalUrl) {
      const active = [...pages].reverse().find((p) => !p.closed && !p.page.isClosed());
      const shotPath = path.join(workDir, "debug_sfl_no_link.png");
      if (active) await active.page.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
      log("total tab yang sempat kebuka:", pages.length, "-- screenshot:", shotPath);
      throw new Error(`Nggak sampai ke link tujuan akhir setelah beberapa putaran. Screenshot: ${shotPath}`);
    }

    console.log(JSON.stringify({ ok: true, redirect_to: finalUrl }));
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
}

main();
