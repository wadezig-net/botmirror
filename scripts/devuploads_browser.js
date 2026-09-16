const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0";

function parseInputs(html) {
  const out = [];
  const re = /<input\b[^>]*>/gi;
  let m;
  while ((m = re.exec(html))) {
    const tag = m[0];
    const n = (tag.match(/name="([^"]+)"/) || [])[1];
    if (!n) continue;
    const v = (tag.match(/value="([^"]*)"/) || [])[1] || "";
    out.push({ name: n, value: v });
  }
  return out;
}

function extractFilename(html, fallback) {
  const m = html.match(/var filename\s*=\s*'([^']+)'/);
  if (m) return m[1];
  const t = html.match(/<input\b[^>]*name="title"[^>]*value="([^"]+)"/i);
  return t ? t[1] : fallback;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  if (!url || !workDir) {
    process.stdout.write(JSON.stringify({ ok: false, error: "Usage: node devuploads_browser.js <url> <work_dir>" }));
    process.exit(1);
  }

  const proxyUrlRaw = process.env.DOWNLOAD_PROXY || undefined;
  let browser;
  try {
    const launchArgs = {
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
      ],
    };
    if (proxyUrlRaw) {
      const pu = new URL(proxyUrlRaw);
      launchArgs.proxy = {
        server: "http://" + pu.hostname + ":" + pu.port,
        username: pu.username || undefined,
        password: pu.password || undefined,
      };
    }
    browser = await chromium.launch(launchArgs);

    const context = await browser.newContext({
      userAgent: UA,
      locale: "en-US",
      timezoneId: "Asia/Jakarta",
      viewport: { width: 1920, height: 1080 },
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();

    // STEP 1: GET devuploads page
    const r1 = await page.request.get(url, { timeout: 60000 });
    if (!r1.ok()) throw new Error("GET file page gagal (HTTP " + r1.status() + ")");
    const t1 = await r1.text();
    const filename = extractFilename(t1, url.split("/").pop() || "file");
    let data = parseInputs(t1);
    if (!data.length) throw new Error("Tidak ada data (file expired / salah URL).");
    if (!data.find(function (e) { return e.name === "ransite"; })) data.push({ name: "ransite", value: "100" });

    const base = url.replace(/^https?:\/\//, "").split("/")[0];
    const low = filename.toLowerCase();
    var safelinks;
    if (low.endsWith(".apk") || low.endsWith(".xapk"))
      safelinks = ["https://gujjukhabar.in/"];
    else if (/(\.pdf|\.epub|\.m4b|\.mp3)$/.test(low))
      safelinks = ["https://smartfeecalculator.com/"];
    else
      safelinks = [
        "https://pdfhindibook.com/",
        "https://gujjukhabar.in/",
        "https://smartfeecalculator.com/",
      ];

    // STEP 2: try safelinks
    var d2 = null;
    var safelink = null;
    for (var si = 0; si < safelinks.length; si++) {
      var s = safelinks[si];
      try {
        var formObj = {};
        data.forEach(function (e) { formObj[e.name] = e.value; });
        const r2 = await page.request.post(s, {
          form: formObj,
          headers: { Origin: "https://" + base, Referer: url },
          timeout: 60000,
        });
        const t2 = await r2.text();
        const inputs = parseInputs(t2);
        if (inputs.find(function (e) { return e.name === "id"; })) {
          d2 = inputs;
          var u = new URL(r2.url());
          safelink = u.origin;
          break;
        }
      } catch (_) {}
    }
    if (!d2) throw new Error("Safelink sponsor tidak merespon form download.");

    // STEP 3: dlhash
    const dlhResp = await page.request.get("https://du2.devuploads.com/dlhash.php", {
      headers: { Origin: safelink, Referer: safelink + "/" },
      timeout: 60000,
    });
    const ipp = (await dlhResp.text()).trim();
    if (!ipp) throw new Error("Gagal mendapatkan ipp.");

    // STEP 4: token
    var rand = "";
    d2.forEach(function (e) { if (e.name === "rand") rand = e.value; });
    const tkResp = await page.request.post(
      "https://devuploads.com/token/token.php",
      {
        form: { rand: rand, msg: "" },
        headers: { Origin: safelink, Referer: safelink + "/" },
        timeout: 60000,
      }
    );
    const xd = (await tkResp.text()).trim();
    if (!xd) throw new Error("Gagal mendapatkan xd.");

    // STEP 5: final POST
    var finalObj = {};
    d2.forEach(function (e) { finalObj[e.name] = e.value; });
    finalObj["ipp"] = ipp;
    finalObj["xd"] = xd;

    const finResp = await page.request.post(url, {
      form: finalObj,
      headers: { Origin: safelink, Referer: safelink + "/" },
      timeout: 60000,
    });
    const finText = await finResp.text();
    const oriM = finText.match(
      /<input\b[^>]*name="orilink"[^>]*value="([^"]+)"/i
    );
    if (!oriM) {
      if (/File Not Found|file no longer|expired/i.test(finText))
        throw new Error("File tidak ditemukan / expired.");
      if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });
      const shotPath = path.join(workDir, "devuploads_debug.html");
      fs.writeFileSync(shotPath, finText.slice(0, 8000));
      throw new Error("Tidak ketemu orilink. Debug: " + shotPath);
    }
    const direct = oriM[1].replace(/&amp;/g, "&");
    process.stdout.write(
      JSON.stringify({
        ok: true,
        direct_url: direct,
        filename: filename,
        referer: "https://" + base,
      })
    );
  } catch (err) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        error: String((err && err.message) || err),
      })
    );
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
}

main();
