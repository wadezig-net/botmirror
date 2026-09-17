// Usage: node fichier_download.js <url> <work_dir>
// Output: JSON satu baris -> {"ok": true, "direct_url": "...", "cookie_file": "...", "referer": "..."}
//                          atau {"ok": false, "error": "...", "code": "guest_slots|rate_limit|no_link|..."}
//
// 1fichier (free user) punya countdown "Free download in N" -> tombol #dlw di-disable
// sampai hitungan habis (baru jadi "Start download" & bisa diklik). Klien headless
// nggak boleh ngeklik tombol sebelum itu (Playwright nolak disabled) -> makanya di sini
// kita TUNGGU sampai #dlw benar-benar enabled, baru klik, lalu lihat hasilnya:
//   - file langsung (response attachment / event download)  -> ok
//   - "guest slots penuh / Sign in and download now"        -> code guest_slots (coba proxy lain)
//   - "you must wait N minutes"                             -> code rate_limit (rotasi proxy)
//
// Bonus: kalau ada FICHIER_LOGIN_COOKIES (akun free/premium), dipakai biar selamat
// dari "guest slots penuh" & rate-limit.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

function log(...args) {
  console.error("[debug]", ...args);
}

let lastWaitBody = "";

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
const WAIT_MIN_RE = /(?:you|vous) must wait (?:between downloads\.?\s*)?(\d+)\s*(?:minute|min)/i;
const GUEST_SLOT_RE = /guest slots?|sign in and download now|free guest|currently in use|reserved to our (free |)members/i;

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

async function waitForStartButton(page, candidateRef, maxWaitMs) {
  // 1) Prioritas: tombol countdown 1fichier #dlw. Tunggu sampai enable & text = "Start download".
  // 2) Fallback: tombol/link apa pun yang enabled dan bertext "Start download".
  // Loop ini juga punya escape: kalau file/direct link sudah ketangkap di network -> berhenti.
  const dlw = page.locator("#dlw");
  const alt = page.locator(
    'button:not([disabled]):has-text("Start download"), ' +
    'a:not([disabled]):has-text("Start download")'
  ).first();
  const deadline = Date.now() + maxWaitMs;

  while (Date.now() < deadline && !candidateRef.value) {
    const dlwCount = await dlw.count().catch(() => 0);
    if (dlwCount > 0) {
      const disabled = await dlw.isDisabled().catch(() => true);
      const txt = (await dlw.textContent().catch(() => "")).trim();
      if (!disabled && /start download|download now|télécharger/i.test(txt)) {
        log("tombol #dlw siap diklik:", txt.slice(0, 60));
        return "dlw";
      }
      if (disabled) {
        log("masih countdown:", (txt || "").slice(0, 40));
      }
    } else {
      const altCount = await alt.count().catch(() => 0);
      if (altCount > 0) {
        const enabled = await alt.isEnabled().catch(() => false);
        if (enabled) {
          log("tombol alternatif 'Start download' siap");
          return "alt";
        }
      }
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  return null;
}

async function classifyResult(page, candidateRef) {
  if (candidateRef.value) return "ok";
  const body = await page.textContent("body").catch(() => "");
  if (WAIT_LIMIT_RE.test(body || "")) return "rate_limit";
  if (GUEST_SLOT_RE.test(body || "")) return "guest_slots";
  return "no_link";
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
        log("cookies login 1fichier berhasil dimuat");
      } catch (e) {
        log("gagal load cookies login:", e.message);
      }
    }

    const candidateRef = { value: null };

    function watchResponses(page, label) {
      page.on("response", (response) => {
        try {
          const resourceType = response.request().resourceType();
          if (looksLikeFileResponse(response)) {
            log(`[${label}] kandidat file ketemu:`, response.url().slice(0, 150));
            if (!candidateRef.value) {
              candidateRef.value = { url: response.url(), filename: filenameFromResponse(response), pageUrl: page.url() };
            }
          } else if (!IGNORED_RESOURCE_TYPES.has(resourceType) && resourceType !== "stylesheet") {
            const h = response.headers();
            log(
              `[${label}] response:`, resourceType,
              "| ct:", h["content-type"] || "?",
              "| cl:", h["content-length"] || "?",
              "|", response.url().slice(0, 100)
            );
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
        await waitForStartButton(popup, candidateRef, 40000);
        if (!candidateRef.value) {
          await popup.close().catch(() => {});
        }
      })();
      popupPromises.push(p);
    });

    log("navigasi ke:", url, proxyServer ? `(proxy: ${proxyServer})` : "(tanpa proxy)");
    await mainPage.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch((e) => log("goto error:", e.message));

    const bodyText = await mainPage.textContent("body").catch(() => "");
    if (WAIT_LIMIT_RE.test(bodyText || "")) {
      lastWaitBody = bodyText || "";
      log("kena rate-limit wait-time di halaman awal");
      return { kind: "rate_limit", candidate: null };
    }
    if (GUEST_SLOT_RE.test(bodyText || "")) {
      log("guest slot penuh sejak halaman awal");
      return { kind: "guest_slots", candidate: null };
    }

    // === STEP A: skip countdown — paksa klik #dlw tanpa nunggu enable. ===
    // 1fichier menahan tombol "Start download" dgn countdown s/d 60 detik buat free
    // user. Ternyata batas itu client-side: hapus atribut disabled lalu force-click
    // langsung menghasilkan link "Start your download" (server nerima POST seketika).
    const dlwF = mainPage.locator("#dlw");
    if ((await dlwF.count().catch(() => 0)) > 0 && !candidateRef.value) {
      const txt0 = (await dlwF.textContent().catch(() => "")).trim();
      await dlwF.evaluate((el) => el.removeAttribute("disabled")).catch(() => {});
      const okCli = await dlwF
        .click({ timeout: 8000, force: true })
        .then(() => true)
        .catch(() => false);
      if (okCli) {
        log("paksa klik #dlw (skip countdown:", (txt0 || "?").slice(0, 32) + ")");
        const linkSel =
          'a:has-text("Start your download"), a:has-text("Click here to download"), a[download]:has-text("Download")';
        const linkItem = mainPage.locator(linkSel).first();
        const grabLink = async () => {
          const href = await linkItem.getAttribute("href").catch(() => null);
          if (href && !candidateRef.value) {
            const fname = await linkItem.getAttribute("download").catch(() => null);
            candidateRef.value = {
              url: href,
              filename: fname || "file",
              pageUrl: mainPage.url(),
            };
            log("skip-countdown sukses, link:", href.slice(0, 110));
            return true;
          }
          return false;
        };
        try {
          await linkItem.waitFor({ state: "attached", timeout: 5000 });
          await grabLink();
        } catch (_) {
          // Server kadang reset countdown -> #dlw langsung "Start download" (enabled).
          await dlwF.waitFor({ state: "attached", timeout: 5000 }).catch(() => {});
          try {
            await dlwF.waitFor({ state: "visible", timeout: 3000 });
            if (!(await dlwF.isDisabled().catch(() => true)) &&
                /start download|download now|télécharger/i.test(await dlwF.textContent().catch(() => ""))) {
              await dlwF.click({ timeout: 8000, force: true }).catch(() => {});
            }
          } catch (_) {}
          try {
            await linkItem.waitFor({ state: "attached", timeout: 9000 });
            await grabLink();
          } catch (_) {}
        }
        if (!candidateRef.value) {
          const bodyNow = await mainPage.textContent("body").catch(() => "");
          if (WAIT_LIMIT_RE.test(bodyNow || "")) {
            lastWaitBody = bodyNow || "";
            return { kind: "rate_limit", candidate: null };
          }
        }
      }
    }

    // === STEP B: fallback countdown normal (kalau STEP A nggak menghasilkan link). ===
    const clicked = candidateRef.value ? null : await waitForStartButton(mainPage, candidateRef, 110000);
    if (clicked) {
      if (clicked === "dlw") {
        await mainPage.locator("#dlw").click({ timeout: 15000, force: true }).catch((e) => log("klik #dlw gagal:", e.message));
      } else {
        await mainPage
          .locator('button:not([disabled]):has-text("Start download"), a:not([disabled]):has-text("Start download")')
          .first()
          .click({ timeout: 15000 })
          .catch((e) => log("klik alternatif gagal:", e.message));
      }
    }

    // === STEP: DOM scan — cari link "Start your download" (tampil setelah klik #dlw). ===
    if (!candidateRef.value) {
      await new Promise((r) => setTimeout(r, 4000));
      const dlLink = mainPage
        .locator('a:has-text("Start your download"), a:has-text("Click here to download"), a[download]:has-text("Download")')
        .first();
      const dlCount = await dlLink.count().catch(() => 0);
      if (dlCount > 0) {
        const href = await dlLink.getAttribute("href").catch(() => null);
        const dlFilename = await dlLink.getAttribute("download").catch(() => null);
        const pageTitle = (await mainPage.textContent("h1").catch(() => "")) || "";
        log("DOM link 'download' ditemukan:", href ? href.slice(0, 120) : "(null)", "| download attr:", dlFilename);
        if (href && !candidateRef.value) {
          candidateRef.value = {
            url: href,
            filename: dlFilename || pageTitle.trim() || "file",
            pageUrl: mainPage.url(),
          };
        }
      }
    }

    // Beri waktu buat response/file ataupun popup muncul.
    for (let i = 0; i < 12 && !candidateRef.value; i++) {
      await new Promise((r) => setTimeout(r, 1000));
    }
    if (!candidateRef.value && popupPromises.length > 0) {
      await Promise.race([
        Promise.all(popupPromises),
        new Promise((r) => setTimeout(r, 15000)),
      ]);
    }

    const kind = await classifyResult(mainPage, candidateRef);
    if (kind !== "ok") {
      const shotPath = path.join(workDir, `debug_fichier_${kind}_${Date.now()}.png`);
      await mainPage.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
      log("hasil:", kind, "| popup:", popupCount, "| screenshot:", shotPath);
      return { kind, candidate: null };
    }

    const cookies = await context.cookies();
    const cookieFile = path.join(workDir, "fichier_cookies.json");
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));

    return {
      kind: "ok",
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
  let sawGuest = false;
  let sawRateLimit = false;

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
      if (result.kind === "guest_slots") {
        sawGuest = true;
        log(proxyServer ? `proxy ${proxyServer} juga guest-slot penuh, lanjut berikutnya` : "guest-slot penuh tanpa proxy, coba proxy berikutnya");
        continue;
      }
      if (result.kind === "rate_limit") {
        sawRateLimit = true;
        log(proxyServer ? `proxy ${proxyServer} kena rate-limit, lanjut berikutnya` : "kena rate-limit tanpa proxy, coba proxy berikutnya");
        continue;
      }
      lastError = new Error("Tidak ketemu link file di traffic network");
    } catch (err) {
      lastError = err;
      log("percobaan gagal total:", err.message || String(err));
    }
  }

  let error;
  if (sawGuest) {
    error =
      "1fichier lagi penuh slot guest (butuh login akun free). " +
      (loginCookiesPath ? "" : "Set cookies login 1fichier di FICHIER_LOGIN_COOKIES biar lolos.");
  } else if (sawRateLimit) {
    const waitMatch = (lastWaitBody || "").match(WAIT_MIN_RE);
    error = `1fichier kena rate-limit (akun free harus nunggu antar download${
      waitMatch ? " " + waitMatch[1] + " menit" : ""
    }). Coba lagi nanti / pakai akun premium.`;
  } else {
    error = `Semua percobaan gagal (${proxies.length} kandidat dicoba). ${lastError ? lastError.message : ""}`.trim();
  }
  console.log(JSON.stringify({ ok: false, error, code: sawGuest ? "guest_slots" : (sawRateLimit ? "rate_limit" : "no_link") }));
  process.exitCode = 1;
}

main();