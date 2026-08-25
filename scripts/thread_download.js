// Usage: node threads_download.js <url> <work_dir>
// Output: JSON satu baris -> {"ok": true, "direct_url": "...", "filename": "...", ...}
//                          atau {"ok": false, "error": "..."}
//
// Strategi berlapis buat threads.com/threads.net (belum didukung yt-dlp per Agustus 2026):
// 1. Coba baca meta tag og:video / og:video:secure_url dari HTML -- situs Meta
//    (Threads/Instagram/Facebook) biasanya nyimpen link video langsung di situ
//    buat keperluan link-preview crawler, nggak butuh render JS penuh.
// 2. Kalau nggak ketemu, coba cari di JSON data yang di-embed di halaman (window.__NEXT_DATA__
//    atau sejenisnya -- Threads pakai Next.js/React, data post sering ada di situ).
// 3. Fallback terakhir: pantau traffic network buat nemuin response video (.mp4)
//    dari domain fbcdn.net/cdninstagram.com yang notabene host media Meta.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

function log(...args) {
  console.error("[debug]", ...args);
}

const MEDIA_DOMAINS = ["fbcdn.net", "cdninstagram.com"];
const AD_TRACKER_DOMAINS = [
  "googlesyndication.com", "doubleclick.net", "google-analytics.com",
  "googletagmanager.com", "facebook.com/tr", "connect.facebook.net",
];

function looksLikeMediaResponse(response) {
  const url = response.url();
  if (AD_TRACKER_DOMAINS.some((d) => url.includes(d))) return false;
  if (!MEDIA_DOMAINS.some((d) => url.includes(d))) return false;

  const resourceType = response.request().resourceType();
  const ct = (response.headers()["content-type"] || "").toLowerCase();

  if (resourceType === "media" || ct.startsWith("video/")) return true;
  if (/\.mp4(\?|$)/i.test(url)) return true;
  return false;
}

async function extractFromMetaTags(page) {
  return await page.evaluate(() => {
    const getMeta = (prop) => {
      const el = document.querySelector(`meta[property="${prop}"]`);
      return el ? el.getAttribute("content") : null;
    };
    return {
      video: getMeta("og:video:secure_url") || getMeta("og:video") || getMeta("og:video:url"),
      image: getMeta("og:image:secure_url") || getMeta("og:image"),
      title: getMeta("og:title") || document.title,
    };
  });
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node threads_download.js <url> <work_dir>" }));
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

    const page = await context.newPage();

    let candidate = null;
    page.on("response", (response) => {
      try {
        if (!candidate && looksLikeMediaResponse(response)) {
          candidate = { url: response.url(), filename: null };
          log("kandidat media dari network:", response.url().slice(0, 150));
        }
      } catch (_) {}
    });

    log("navigasi ke:", url);
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => log("goto error:", e.message));
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => log("networkidle timeout, lanjut aja"));

    // langkah 1: coba meta tag dulu (paling cepat & stabil, nggak butuh nunggu apa-apa)
    const meta = await extractFromMetaTags(page).catch(() => ({}));
    log("meta tag hasil:", JSON.stringify(meta));

    let finalUrl = null;
    let title = (meta && meta.title) || "threads_post";

    if (meta && meta.video) {
      finalUrl = meta.video;
      log("pakai video dari meta tag");
    } else {
      // langkah 2: kasih jeda buat video player lazy-load (kalau post-nya video),
      // baru cek kandidat hasil pantauan network
      for (let i = 0; i < 10 && !candidate; i++) {
        await new Promise((r) => setTimeout(r, 1000));
      }
      if (candidate) {
        finalUrl = candidate.url;
        log("pakai video dari network capture");
      } else if (meta && meta.image) {
        // fallback: post-nya foto doang, bukan video
        finalUrl = meta.image;
        log("nggak ada video, pakai gambar dari meta tag");
      }
    }

    if (!finalUrl) {
      const shotPath = path.join(workDir, "debug_threads_no_media.png");
      await page.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
      throw new Error(`Nggak ketemu media (video/gambar) di post ini. Screenshot: ${shotPath}`);
    }

    const cookies = await context.cookies();
    const cookieFile = path.join(workDir, "threads_cookies.json");
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));

    const ext = finalUrl.includes(".mp4") ? "mp4" : (finalUrl.match(/\.(jpg|jpeg|png|webp)/i) || [, "jpg"])[1];
    const safeTitle = String(title).replace(/[\\/*?:"<>|]/g, "").slice(0, 80) || "threads_post";

    console.log(JSON.stringify({
      ok: true,
      direct_url: finalUrl,
      filename: `${safeTitle}.${ext}`,
      referer: url,
      cookie_file: cookieFile,
    }));
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
}

main();
