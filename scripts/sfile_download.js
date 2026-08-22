// Usage: node sfile_download.js <url> <work_dir>
// Output: JSON satu baris -> {"ok": true, "direct_url": "...", "cookie_file": "...", "referer": "..."}
//                          atau {"ok": false, "error": "..."}
//
// Strategi baru: daripada nyoba nangkep event "download" di browser (rewel karena
// beda situs beda cara trigger-nya -- auto, klik, popup, dst), kita PANTAU semua
// response network yang lewat, cari yang polanya kayak file asli (Content-Disposition
// attachment, atau URL besar dengan ekstensi file/content-type biner). Begitu ketemu,
// kita EXPORT link + cookies session-nya, biar file-nya didownload di sisi Python
// pakai requests biasa -- jauh lebih stabil daripada download lewat headless browser.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

function log(...args) {
  console.error("[debug]", ...args);
}

const BINARY_EXT_RE = /\.(zip|rar|7z|apk|mp4|mkv|avi|mov|exe|iso|pdf|docx?|xlsx?|pptx?|bin|dmg|tar|gz)(\?|$)/i;

// domain iklan/tracker umum -- jangan pernah dianggap kandidat file, sesering
// apapun konten-type/ukurannya keliatan "mirip" file beneran
const AD_TRACKER_DOMAINS = [
  "googlesyndication.com", "doubleclick.net", "google-analytics.com",
  "googletagmanager.com", "googletagservices.com", "gstatic.com",
  "adnxs.com", "facebook.net", "connect.facebook.net", "amazon-adsystem.com",
  "criteo.com", "taboola.com", "outbrain.com", "popads.net",
  "propellerads.com", "adsterra.com", "adservice.google.com",
  "google.com/pagead", "googleadservices.com",
];

// resource type yang HAMPIR PASTI bukan file yang kita cari, walau content-type-nya
// kebetulan mirip biner (banyak ad-script nyamar pakai content-type aneh)
const IGNORED_RESOURCE_TYPES = new Set(["script", "stylesheet", "image", "font", "media", "eventsource", "websocket", "manifest"]);

function looksLikeFileResponse(response) {
  const url = response.url();

  if (AD_TRACKER_DOMAINS.some((d) => url.includes(d))) return false;

  const resourceType = response.request().resourceType();
  const headers = response.headers();
  const cd = headers["content-disposition"] || "";
  const ct = (headers["content-type"] || "").toLowerCase();
  const cl = parseInt(headers["content-length"] || "0", 10);

  // Content-Disposition: attachment adalah sinyal PALING valid -- situs secara
  // eksplisit bilang "ini file buat didownload", nggak peduli resource type-nya apa
  if (/attachment/i.test(cd)) return true;

  if (IGNORED_RESOURCE_TYPES.has(resourceType)) return false;

  // hindari nyamber file text/html atau JSON kecil (respons API biasa, bukan file)
  if (ct.startsWith("text/") || ct.includes("json") || ct.includes("javascript")) return false;

  if (BINARY_EXT_RE.test(url) && (cl === 0 || cl > 100 * 1024)) return true;

  const strictBinaryTypes = [
    "application/octet-stream", "application/zip", "application/x-rar-compressed",
    "application/vnd.android.package-archive", "application/x-msdownload",
    "application/x-7z-compressed", "video/", "application/pdf",
  ];
  // cl===0 sering berarti server kirim pakai chunked transfer (nggak nyantumin
  // content-length di awal) -- ini NORMAL buat file besar, jangan ditolak
  if (strictBinaryTypes.some((t) => ct.startsWith(t)) && (cl === 0 || cl > 100 * 1024)) return true;

  return false;
}

function filenameFromResponse(response) {
  const headers = response.headers();
  const cd = headers["content-disposition"] || "";
  const match = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  if (match) return decodeURIComponent(match[1]);
  return response.url().split("/").pop().split("?")[0] || "downloaded_file";
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node sfile_download.js <url> <work_dir>" }));
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
      acceptDownloads: true,
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    });

    await context.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });

    let candidate = null;

    function watchResponses(page, label) {
      page.on("response", (response) => {
        try {
          const resourceType = response.request().resourceType();
          if (!IGNORED_RESOURCE_TYPES.has(resourceType) && resourceType !== "stylesheet") {
            const h = response.headers();
            log(
              `[${label}] response:`, resourceType,
              "| ct:", h["content-type"] || "?",
              "| cl:", h["content-length"] || "?",
              "|", response.url().slice(0, 100)
            );
          }
          if (looksLikeFileResponse(response)) {
            log(`[${label}] kandidat file ketemu:`, response.url().slice(0, 150));
            if (!candidate) {
              candidate = { url: response.url(), filename: filenameFromResponse(response), pageUrl: page.url() };
            }
          }
        } catch (_) {}
      });
      // tetep dengerin event download juga, siapa tau situsnya kooperatif
      page.on("download", (download) => {
        if (!candidate) {
          candidate = { url: download.url(), filename: download.suggestedFilename(), pageUrl: page.url() };
          log(`[${label}] event download langsung kasih url:`, candidate.url.slice(0, 150));
        }
      });
    }

    const mainPage = await context.newPage();
    watchResponses(mainPage, "main");

    let popupCount = 0;
    context.on("page", async (popup) => {
      popupCount++;
      const label = `popup${popupCount}`;
      watchResponses(popup, label);
      await popup.waitForLoadState("domcontentloaded", { timeout: 8000 }).catch(() => {});
      await new Promise((r) => setTimeout(r, 8000));
      if (!candidate) {
        await popup.close().catch(() => {});
      }
    });

    log("navigasi ke:", url);
    await mainPage.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => log("goto error:", e.message));
    // tunggu network reda (JS-heavy page kayak gini butuh waktu lebih buat render
    // elemen dinamis) -- kalau timeout ya udah lanjut aja, jangan gagalin proses
    await mainPage.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => log("networkidle timeout, lanjut aja"));

    // tunggu auto-trigger dulu (diperpanjang -- VPS kadang lebih lambat render JS)
    for (let i = 0; i < 15 && !candidate; i++) {
      await new Promise((r) => setTimeout(r, 1000));
    }

    // beberapa file-host (termasuk sfile) butuh KLIK BERULANG di beberapa halaman
    // berturut-turut (klik di halaman file -> pindah ke halaman "generate link" ->
    // klik lagi di situ buat beneran mulai download). Makanya coba klik sampai
    // beberapa kali, di halaman manapun tombolnya lagi kelihatan saat itu.
    for (let attempt = 1; attempt <= 3 && !candidate; attempt++) {
      const button = mainPage
        .locator('a:has-text("Download File"), button:has-text("Download File"), a:has-text("Download")')
        .first();
      const isVisible = await button.isVisible({ timeout: 20000 }).catch(() => false);
      log(`percobaan klik ke-${attempt}, tombol visible?`, isVisible, "| url saat ini:", mainPage.url());

      if (isVisible) {
        await button.click({ timeout: 10000 }).catch((e) => log("klik gagal:", e.message));
      } else {
        const count = await button.count().catch(() => 0);
        if (count > 0) {
          await button.click({ timeout: 10000, force: true }).catch((e) => log("force klik gagal:", e.message));
        } else {
          log(`percobaan ke-${attempt}: tombol nggak ketemu sama sekali, stop nyoba klik lagi`);
          break;
        }
      }

      // kasih waktu buat navigasi (kalau ada) dan buat request file mulai kalau
      // ini klik yang beneran micu download
      for (let i = 0; i < 12 && !candidate; i++) {
        await new Promise((r) => setTimeout(r, 1000));
      }
    }

    if (!candidate) {
      const shotPath = path.join(workDir, "debug_no_link_found.png");
      await mainPage.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
      log("total popup terbuka:", popupCount, "-- screenshot:", shotPath);
      throw new Error(`Tidak ketemu link file di traffic network. Screenshot: ${shotPath}`);
    }

    // export cookies context (dibutuhin kalau link-nya butuh session buat diakses)
    const cookies = await context.cookies();
    const cookieFile = path.join(workDir, "sfile_cookies.json");
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));

    console.log(JSON.stringify({
      ok: true,
      direct_url: candidate.url,
      filename: candidate.filename,
      referer: candidate.pageUrl,
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
