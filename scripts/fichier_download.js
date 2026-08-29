// Usage: node fichier_download.js <url> <work_dir>
// Output: JSON satu baris -> {"ok": true, "direct_url": "...", "cookie_file": "...", "referer": "..."}
//                          atau {"ok": false, "error": "..."}
//
// 1fichier (free user) sering kena rate-limit "you must wait N minutes" per-IP.
// Strategi: kalau ada FICHIER_LOGIN_COOKIES (akun Premium), dipakai dulu (Premium
// nggak kena limit ini sama sekali). Kalau nggak ada, coba tanpa proxy dulu; kalau
// halaman nunjukkin pesan wait-limit, ganti IP pakai proxy dari FICHIER_PROXIES_FILE
// (satu per baris) dan ulangi, sampai berhasil atau daftar proxy habis.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

function log(...args) {
  console.error("[debug]", ...args);
}

const BINARY_EXT_RE = /\.(zip|rar|7z|apk|mp4|mkv|avi|mov|exe|iso|pdf|docx?|xlsx?|pptx?|bin|dmg|tar|gz)(\?|$)/i;

const AD_TRACKER_DOMAINS = [
  "googlesyndication.com", "doubleclick.net", "google-analytics.com",
  "googletagmanager.com", "googletagservices.com", "gstatic.com",
  "adnxs.com", "facebook.net", "connect.facebook.net", "amazon-adsystem.com",
  "criteo.com", "taboola.com", "outbrain.com", "popads.net",
  "propellerads.com", "adsterra.com", "adservice.google.com",
  "google.com/pagead", "googleadservices.com",
];

const IGNORED_RESOURCE_TYPES = new Set(["script", "stylesheet", "image", "font", "media", "eventsource", "websocket", "manifest"]);

const WAIT_LIMIT_RE = /you must wait|vous devez attendre/i;

function looksLikeFileResponse(response) {
  const url = response.url();
  if (AD_TRACKER_DOMAINS.some((d) => url.includes(d))) return false;

  const resourceType = response.request().resourceType();
  const headers = response.headers();
  const cd = headers["content-disposition"] || "";
  const ct = (headers["content-type"] || "").toLowerCase();
  const cl = parseInt(headers["content-length"] || "0", 10);

  if (/attachment/i.test(cd)) return true;
  if (IGNORED_RESOURCE_TYPES.has(resourceType)) return false;
  if (ct.startsWith("text/") || ct.includes("json") || ct.includes("javascript")) return false;
  if (BINARY_EXT_RE.test(url) && (cl === 0 || cl > 100 * 1024)) return true;

  const strictBinaryTypes = [
    "application/octet-stream", "application/zip", "application/x-rar-compressed",
    "application/vnd.android.package-archive", "application/x-msdownload",
    "application/x-7z-compressed", "video/", "application/pdf",
  ];
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

function loadProxyList() {
  const listPath = process.env.FICHIER_PROXIES_FILE;
  if (!listPath || !fs.existsSync(listPath)) return [];
  return fs.readFileSync(listPath, "utf-8")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
}

async function clickDownloadLoop(page, candidateRef, maxAttempts, waitPerAttemptSec) {
  for (let attempt = 1; attempt <= maxAttempts && !candidateRef.value; attempt++) {
    const button = page
      .locator(
        'a:has-text("Click here to download"), button:has-text("Click here to download"), ' +
        'a:has-text("Télécharger"), button:has-text("Télécharger"), ' +
        'a:has-text("Start download"), button:has-text("Start download"), ' +
        'a:has-text("Download"), button:has-text("Download")'
      )
      .first();
    const isVisible = await button.isVisible({ timeout: 15000 }).catch(() => false);
    log(`percobaan klik ke-${attempt}, tombol visible?`, isVisible, "| url saat ini:", page.url());

    if (isVisible) {
      await button.click({ timeout: 10000 }).catch((e) => log("klik gagal:", e.message));
    } else {
      const count = await button.count().catch(() => 0);
      if (count > 0) {
        await button.click({ timeout: 10000, force: true }).catch((e) => log("force klik gagal:", e.message));
      } else {
        log(`percobaan ke-${attempt}: tombol nggak ketemu, tunggu lalu coba lagi`);
      }
    }

    for (let i = 0; i < waitPerAttemptSec && !candidateRef.value; i++) {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
}

async function tryOnce(url, workDir, proxyServer, loginCookiesPath) {
  let browser;
  try {
    const launchOpts = {
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
    };
    if (proxyServer) launchOpts.proxy = { server: proxyServer };

    browser = await chromium.launch(launchOpts);

    const context = await browser.newContext({
      acceptDownloads: true,
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    });

    await context.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });

    if (loginCookiesPath && fs.existsSync(loginCookiesPath)) {
      try {
        const loginCookies = JSON.parse(fs.readFileSync(loginCookiesPath, "utf-8"));
        await context.addCookies(loginCookies);
        log("cookies premium 1fichier berhasil dimuat");
      } catch (e) {
        log("gagal load cookies premium:", e.message);
      }
    }

    const candidateRef = { value: null };

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
            if (!candidateRef.value) {
              candidateRef.value = { url: response.url(), filename: filenameFromResponse(response), pageUrl: page.url() };
            }
          }
        } catch (_) {}
      });
      page.on("download", (download) => {
        if (!candidateRef.value) {
          candidateRef.value = { url: download.url(), filename: download.suggestedFilename(), pageUrl: page.url() };
          log(`[${label}] event download langsung kasih url:`, candidateRef.value.url.slice(0, 150));
        }
      });
    }

    const mainPage = await context.newPage();
    watchResponses(mainPage, "main");

    let popupCount = 0;
    const popupPromises = [];
    context.on("page", (popup) => {
      popupCount++;
      const label = `popup${popupCount}`;
      watchResponses(popup, label);
      const p = (async () => {
        await popup.waitForLoadState("domcontentloaded", { timeout: 8000 }).catch(() => {});
        // popup 1fichier kadang punya tombol download SENDIRI yang butuh diklik lagi
        await clickDownloadLoop(popup, candidateRef, 2, 10);
        if (!candidateRef.value) {
          await popup.close().catch(() => {});
        }
      })();
      popupPromises.push(p);
    });

    log("navigasi ke:", url, proxyServer ? `(proxy: ${proxyServer})` : "(tanpa proxy)");
    await mainPage.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => log("goto error:", e.message));
    await mainPage.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => log("networkidle timeout, lanjut aja"));

    const bodyText = await mainPage.textContent("body").catch(() => "");
    if (WAIT_LIMIT_RE.test(bodyText || "")) {
      log("kena rate-limit wait-time di halaman ini");
      return { rateLimited: true, candidate: null };
    }

    for (let i = 0; i < 10 && !candidateRef.value; i++) {
      await new Promise((r) => setTimeout(r, 1000));
    }

    await clickDownloadLoop(mainPage, candidateRef, 4, 20);

    // kasih waktu popup yang lagi diproses buat selesai sebelum kita nyerah
    if (!candidateRef.value && popupPromises.length > 0) {
      await Promise.race([
        Promise.all(popupPromises),
        new Promise((r) => setTimeout(r, 15000)),
      ]);
    }

    if (!candidateRef.value) {
      const shotPath = path.join(workDir, `debug_fichier_no_link_${Date.now()}.png`);
      await mainPage.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
      log("total popup terbuka:", popupCount, "-- screenshot:", shotPath);
      return { rateLimited: false, candidate: null };
    }

    const cookies = await context.cookies();
    const cookieFile = path.join(workDir, "fichier_cookies.json");
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));

    return {
      rateLimited: false,
      candidate: {
        url: candidateRef.value.url,
        filename: candidateRef.value.filename,
        referer: candidateRef.value.pageUrl,
        cookieFile,
      },
    };
  } finally {
    if (browser) await browser.close();
  }
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node fichier_download.js <url> <work_dir>" }));
    process.exit(1);
  }
  if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });

  const loginCookiesPath = process.env.FICHIER_LOGIN_COOKIES;
  const proxies = [null, ...loadProxyList()]; // null = coba tanpa proxy dulu
  log(`total kandidat percobaan: ${proxies.length} (1 direct + ${proxies.length - 1} proxy)`);

  let lastError = null;

  try {
    for (const proxyServer of proxies) {
      try {
        const result = await tryOnce(url, workDir, proxyServer, loginCookiesPath);
        if (result.candidate) {
          console.log(JSON.stringify({
            ok: true,
            direct_url: result.candidate.url,
            filename: result.candidate.filename,
            referer: result.candidate.referer,
            cookie_file: result.candidate.cookieFile,
          }));
          return;
        }
        if (result.rateLimited) {
          log(proxyServer ? `proxy ${proxyServer} juga kena limit, lanjut ke berikutnya` : "kena limit tanpa proxy, coba proxy berikutnya");
          continue;
        }
        // nggak rate-limited tapi juga nggak ketemu kandidat -> kemungkinan situs berubah/error lain,
        // tetap lanjut coba proxy berikutnya siapa tau IP ini yang bermasalah
        lastError = new Error("Tidak ketemu link file di traffic network");
      } catch (err) {
        lastError = err;
        log("percobaan gagal total:", err.message || String(err));
      }
    }

    console.log(JSON.stringify({
      ok: false,
      error: `Semua percobaan gagal (${proxies.length} kandidat dicoba). ${lastError ? lastError.message : ""}`.trim(),
    }));
    process.exitCode = 1;
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
    process.exitCode = 1;
  }
}

main();
