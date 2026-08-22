// Usage: node sfile_download.js <url> <work_dir>
// Output: JSON satu baris ke stdout -> {"ok": true, "path": "..."} atau {"ok": false, "error": "..."}
//
// sfile.co/sfile.mobi seringkali AUTO-DOWNLOAD begitu halaman file selesai dimuat
// (lewat script inline di halaman, tanpa perlu klik apapun). Tombol "Download File"
// cuma cadangan kalau auto-download-nya gagal. Makanya listener event "download"
// HARUS dipasang SEBELUM page.goto(), bukan sesudah -- kalau dipasang sesudah,
// auto-download yang sudah terpicu duluan saat loading bakal kelewat.

const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const DOWNLOAD_BUTTON_SELECTOR =
  'a:has-text("Download File"), button:has-text("Download File"), ' +
  'a:has-text("Download Now"), button:has-text("Download Now"), ' +
  'a:has-text("Download"), button:has-text("Download")';

async function trySaveScreenshot(page, workDir, label) {
  try {
    const shotPath = path.join(workDir, `debug_${label}.png`);
    await page.screenshot({ path: shotPath, fullPage: true }).catch(() => {});
  } catch (_) {
    // abaikan -- screenshot cuma buat debugging
  }
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: "Usage: node sfile_download.js <url> <work_dir>" }));
    process.exit(1);
  }

  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }

  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    });

    const context = await browser.newContext({
      acceptDownloads: true,
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    });

    let mainPage = await context.newPage();
    context.on("page", async (popup) => {
      if (popup === mainPage) return;
      try {
        await popup.waitForLoadState("domcontentloaded", { timeout: 5000 }).catch(() => {});
        await popup.close();
      } catch (_) {}
    });

    // KUNCI PERBAIKAN: pasang listener SEBELUM goto, jalankan bareng (Promise.all)
    // biar auto-download yang terpicu di tengah proses loading tetap tertangkap.
    const downloadPromise = mainPage.waitForEvent("download", { timeout: 45000 }).catch(() => null);

    await mainPage.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {});

    let download = await downloadPromise;

    // fallback: kalau ternyata nggak auto-download, klik tombolnya manual
    if (!download) {
      const downloadPromise2 = mainPage.waitForEvent("download", { timeout: 30000 }).catch(() => null);
      const button = mainPage.locator(DOWNLOAD_BUTTON_SELECTOR).first();
      const isVisible = await button.isVisible({ timeout: 10000 }).catch(() => false);
      if (isVisible) {
        await button.click({ timeout: 10000 }).catch(() => {});
      }
      download = await downloadPromise2;
    }

    if (!download) {
      await trySaveScreenshot(mainPage, workDir, "no_download_triggered");
      throw new Error(
        "File tidak kunjung ke-download (auto maupun manual). " +
        `Screenshot disimpan di ${workDir}/debug_no_download_triggered.png`
      );
    }

    const downloadedPath = path.join(workDir, download.suggestedFilename() || "sfile_download.bin");
    await download.saveAs(downloadedPath);

    console.log(JSON.stringify({ ok: true, path: downloadedPath }));
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
}

main();
