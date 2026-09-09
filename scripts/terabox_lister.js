/**
 * Terabox File Lister - Traverse & List Only
 *
 * Fokus: traverse folder, dapat daftar semua file, kirim ke Telegram status.
 * TIDAK mencoba resolve dlink atau download.
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  BROWSER_TIMEOUT: 30000,
  API_TIMEOUT: 30000,
};

function log(...args) {
  console.error('[lister]', new Date().toISOString(), ...args);
}

function extractSurl(url) {
  let m = url.match(/[?&]surl=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  m = url.match(/\/([a-zA-Z0-9_-]{6,32})\/?$/);
  return m ? m[1] : null;
}

async function fetchWithHeaders(url, cookies, referer) {
  const headers = {
    'User-Agent': CONFIG.UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': referer || url,
    'Cookie': cookies.map(c => `${c.name}=${c.value}`).join('; '),
  };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CONFIG.API_TIMEOUT);
  try {
    const response = await fetch(url, { headers, signal: controller.signal });
    return { status: response.status, body: await response.text() };
  } finally {
    clearTimeout(timeout);
  }
}

function parseApiResponse(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function formatSize(bytes) {
  if (!bytes || bytes === '0') return '0 B';
  const num = parseInt(bytes, 10);
  if (num < 1024) return `${num} B`;
  if (num < 1024 * 1024) return `${(num/1024).toFixed(1)} KB`;
  if (num < 1024 * 1024 * 1024) return `${(num/(1024*1024)).toFixed(1)} MB`;
  return `${(num/(1024*1024*1024)).toFixed(2)} GB`;
}

async function traverseFolder(surl, cookies, baseUrl, dirPath, depth = 0) {
  if (depth > 10) return [];
  const params = new URLSearchParams({
    app_id: '250528', channel: 'chunlei', web: '1', clienttype: '0',
    shorturl: surl, root: '0', dir: dirPath || '/',
  });
  const result = await fetchWithHeaders(
    `https://www.terabox.com/share/list?${params}`,
    cookies, `${baseUrl}/sharing/link?surl=${surl}`
  );
  const data = parseApiResponse(result.body);
  if (!data || data.errno !== 0 || !data.list) {
    log(`Gagal traverse ${dirPath}: errno=${data?.errno}`);
    return [];
  }
  const allFiles = [];
  for (const item of data.list) {
    if (String(item.isdir) === '1') {
      const sub = await traverseFolder(surl, cookies, baseUrl, item.path, depth + 1);
      allFiles.push(...sub);
    } else {
      allFiles.push({ ...item, fullPath: item.path, parentPath: dirPath });
    }
  }
  return allFiles;
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];

  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: 'Usage: node script.js <url> <work_dir>' }));
    process.exit(1);
  }
  if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });

  log('Terabox File Lister - Memulai...');
  log('URL:', url);

  const surl = extractSurl(url);
  if (!surl) {
    console.log(JSON.stringify({ ok: false, error: 'Tidak bisa ekstrak surl dari URL' }));
    process.exit(1);
  }

  log('Surl:', surl);
  const urlObj = new URL(url);
  const baseUrl = `${urlObj.protocol}//${urlObj.hostname}`;

  let browser;
  browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
  });

  try {
    const context = await browser.newContext({
      userAgent: CONFIG.UA,
      locale: 'en-US',
      viewport: { width: 1920, height: 1080 },
    });

    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });

    const page = await context.newPage();
    page.on('dialog', async d => d.dismiss().catch(() => {}));

    log('Navigasi ke halaman...');
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: CONFIG.BROWSER_TIMEOUT }).catch(e => {});
    await page.waitForTimeout(5000);

    let cookies = await context.cookies();
    log(`Dapat ${cookies.length} cookies`);

    if (cookies.length === 0) {
      await page.waitForTimeout(5000);
      cookies = await context.cookies();
      log(`Setelah retry: ${cookies.length} cookies`);
    }

    if (cookies.length === 0) {
      throw new Error('Tidak dapat session cookies');
    }

    fs.writeFileSync(path.join(workDir, 'terabox_cookies.json'), JSON.stringify(cookies));
    log('Cookies disimpan');

    // Tutup browser setelah dapat cookies
    await browser.close();
    browser = null;

    // Dapatkan info root
    log('Mendapatkan daftar dari root...');
    const rootParams = new URLSearchParams({
      app_id: '250528', channel: 'chunlei', web: '1', clienttype: '0',
      shorturl: surl, root: '1',
    });
    const rootRes = await fetchWithHeaders(
      `https://www.terabox.com/share/list?${rootParams}`,
      cookies, `${baseUrl}/sharing/link?surl=${surl}`
    );
    const rootData = parseApiResponse(rootRes.body);
    const rootName = rootData?.title || 'Folder';
    const rootFolders = rootData?.list?.filter(i => String(i.isdir) === '1') || [];

    log(`Root: ${rootName}, Folder: ${rootFolders.length}`);

    // Traverse semua folder
    log('Memulai traverse folder...');
    const allFiles = [];

    for (const folder of rootFolders) {
      log(`\nTraverse: ${folder.server_filename || folder.name}`);
      const files = await traverseFolder(surl, cookies, baseUrl, folder.path, 0);
      allFiles.push(...files);
    }

    const totalFiles = allFiles.length;
    log(`\n=== Total: ${totalFiles} file ===`);

    // Format daftar untuk Telegram
    const fileListLines = [];
    fileListLines.push(`📁 **${rootName}**`);
    fileListLines.push(`\n📄 Total: ${totalFiles} file`);
    fileListLines.push(`\n--- Daftar File ---\n`);

    for (let i = 0; i < totalFiles; i++) {
      const item = allFiles[i];
      const name = item.server_filename || item.name || 'unknown';
      const size = formatSize(item.size);
      const filePath = item.path || '';
      fileListLines.push(`${i+1}. 📄 ${name} (${size})`);
      if (filePath) fileListLines.push(`   ${filePath}`);
    }

    const textForTelegram = fileListLines.join('\n');

    // Output JSON
    const output = {
      ok: true,
      type: 'file_list',
      title: rootName,
      totalFiles: totalFiles,
      cookie_file: path.join(workDir, 'terabox_cookies.json'),
      referer: `${baseUrl}/sharing/link?surl=${surl}`,
      file_list: allFiles.map(f => ({
        filename: f.server_filename || f.name || 'unknown',
        size: formatSize(f.size),
        path: f.path || '',
        fsid: f.fs_id || f.fsid || '',
      })),
      text_for_telegram: textForTelegram,
    };

    console.log(JSON.stringify(output));

    // Simpan ke file
    fs.writeFileSync(path.join(workDir, 'file_list.json'), JSON.stringify(output.file_list, null, 2));
    fs.writeFileSync(path.join(workDir, 'file_list_text.txt'), textForTelegram);

    log(`\nFile list disimpan ke: ${workDir}`);

  } catch (err) {
    log('ERROR:', err.message);
    if (browser) { try { await browser.close(); } catch {} }
    console.log(JSON.stringify({ ok: false, error: String(err.message) }));
    process.exit(1);
  } finally {
    if (browser) { try { await browser.close(); } catch {} }
  }
}

main();
