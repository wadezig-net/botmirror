// Usage (two-pass, dipanggil dari Python downloader/captcha.py + sfl.py):
//   Pass 1 (detect): node sfl_captcha.js <url> <work_dir>
//       -> {"ok":true,"captcha":true,"sitekey":"...","pageurl":"...","action":...}
//          kalau gerbang nampilin Cloudflare Turnstile;
//          atau {"ok":true,"redirect_to":"https://sfile.mobi/..."} kalau lulus
//          tanpa captcha / langsung nemu tujuan akhir;
//          atau {"ok":false,"error":"..."}.
//   Pass 2 (solve): node sfl_captcha.js <url> <work_dir> <token>
//       -> inject token ke input[name=cf-turnstile-response], lanjut alur
//          scroll+klik+timer sampai finalUrl ketemu, output seperti pass 1
//          yang berhasil (redirect_to / direct_url).
//
// alur & selector niru sfl_download.js yang sudah terbukti jalan buat gerbang
// sfl.gl: scroll ke bawah -> klik tombol download -> tunggu countdown -> cek
// semua tab. Bedanya: script ini NGERTI Turnstile (deteksi + inject solusi).

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

// deteksi Turnstile: cari input cf-turnstile-response + data-sitekey di
// elemen cf-turnstile / iframe challenges.cloudflare.com
async function detectTurnstile(page) {
  const info = await page.evaluate(() => {
    const out = { present: false, sitekey: null, action: null, data: null, pagedata: null };

    // 1) elemen widget standar (div.cf-turnstile / div[data-sitekey])
    const el = document.querySelector('.cf-turnstile, .g-recaptcha, [data-sitekey]');
    if (el) {
      out.present = true;
      out.sitekey = el.getAttribute('data-sitekey');
      out.action = el.getAttribute('data-action') || null;
      // cData / chlPageData jarang ada di widget statis; kalau perlu, bisanya
      // dari intercept turnstile.render (di luar cakupan pass ini).
    }

    // 2) kalau nggak ketemu elemen, cek iframe turnstile challenge
    if (!out.sitekey) {
      const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com/turnstile"]');
      if (iframe) {
        out.present = true;
        const m = iframe.src.match(/sitekey=([^&]+)/);
        if (m) out.sitekey = decodeURIComponent(m[1]);
      }
    }

    return out;
  });

  // fallback: baca dari cookie header turnstile kalau elemen belum render
  if (!info.sitekey && page.url().includes("captcha")) {
    log("Turnstile terdeteksi di URL tapi sitekey belum render, coba wait...");
    await page.waitForTimeout(1500);
    return detectTurnstile(page);
  }

  return info;
}

async function injectToken(page, token) {
  await page.evaluate((tok) => {
    const input = document.querySelector('input[name="cf-turnstile-response"], input[name="g-recaptcha-response"]');
    if (input) input.value = tok;
    // set juga ke elem widget kalau ada biar JS form sempat baca sebelum submit
    const el = document.querySelector('.cf-turnstile, [data-sitekey]');
    if (el && el.setAttribute) {
      try { el.setAttribute('data-cf-turnstile-response', tok); } catch (_) {}
    }
  }, token);
}

async function scrollAndClick(page, label) {
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

async function traverseGate(mainPage, pages, token) {
  // after pass-2 inject token, lanjut alur scroll+klik 2 putaran (sama kayak
  // script asli) sampe finalUrl ketemu atau habis percobaan.
  let finalUrl = null;

  if (isFinalDestination(mainPage.url())) {
    finalUrl = mainPage.url();
  }

  for (let round = 1; round <= 2 && !finalUrl; round++) {
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

    // kalau kita udah punya token, inject SEBELUM klik biar form terlanjur
    // bawa value cf-turnstile-response pas submit.
    if (token) {
      await injectToken(active.page, token);
      log(`[${active.label}] token di-inject`);
    }

    await scrollAndClick(active.page, active.label);

    for (let i = 0; i < 28 && !finalUrl; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      for (const p of pages) {
        if (p.closed || p.page.isClosed()) continue;
        const u = p.page.url();
        if (isFinalDestination(u)) {
          finalUrl = u;
          log(`finalUrl ketemu di [${p.label}]:`, u);
          break;
        }
      }
      // kadang setelah captcha kelar, halaman butuh 1-2 detik buat auto-klik
      // tombolnya sendiri — mau murah, laku; mau susah, ya... ini doang.
    }
  }

  return finalUrl;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  const token = process.argv[4] || null; // optional: pass-2 solved token

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node sfl_captcha.js <url> <work_dir> [token]" }));
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
    await mainPage.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});

    // PASS 1 tanpa token: kalau ada Turnstile, balikin parameternya buat
    // 2Captcha solve dulu (dari sini Python lanjut pass 2).
    if (!token) {
      const ts = await detectTurnstile(mainPage);
      if (ts.present && ts.sitekey) {
        log("Turnstile terdeteksi:", JSON.stringify(ts));
        console.log(JSON.stringify({
          ok: true,
          captcha: {
            type: "turnstile",
            sitekey: ts.sitekey,
            pageurl: mainPage.url(),
            action: ts.action,
            data: ts.data,
            pagedata: ts.pagedata,
          },
        }));
        return;
      }
    }

    // Infusi token atau gate tanpa captcha -> lanjut traverse.
    const finalUrl = await traverseGate(mainPage, pages, token);

    if (!finalUrl) {
      const active = [...pages].reverse().find((p) => !p.closed && !p.page.isClosed());
      const shotPath = path.join(workDir, "debug_sfl_captcha_no_link.png");
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