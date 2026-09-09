/**
 * Terabox Single Browser - Process all files in one browser session
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
  console.error('[single]', new Date().toISOString(), ...args);
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
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    root: '0',
    dir: dirPath || '/',
  });
  const result = await fetchWithHeaders(
    `https://www.terabox.com/share/list?${params}`,
    cookies,
    `${baseUrl}/sharing/link?surl=${surl}`
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
      allFiles.push({ ...item, isFolderItem: true, fullPath: item.path });
    } else {
      allFiles.push({ ...item, fullPath: item.path, parentPath: dirPath });
    }
  }
  return allFiles;
}

async function resolveDlinkInBrowser(page, item, surl, baseUrl) {
  const fsid = item.fs_id || item.fsid;
  if (!fsid) return null;
  
  log(`  Mencoba resolve di browser: ${item.server_filename || item.name}`);
  
  try {
    const params = new URLSearchParams({
      fs_id: String(fsid),
      sign: String(item.sign || ''),
      timestamp: String(item.timestamp || ''),
      web: '1',
      app_id: '250528',
    });
    
    const linkUrl = `${baseUrl}/share/download?${params}`;
    await page.goto(linkUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e => {});
    await page.waitForTimeout(3000);
    
    const dlUrl = await page.evaluate(() => {
      const a = document.querySelector('a[download]');
      if (a && a.href) return a.href;
      const btn = document.querySelector('a.btn-download, a.btn_primary, .download-btn');
      if (btn && btn.href) return btn.href;
      return null;
    });
    
    if (dlUrl) {
      log(`  ✓ Dapat direct link: ${dlUrl.substring(0, 80)}...`);
      return dlUrl;
    }
    
    log(`  ! Tidak ada download link di halaman`);
    return null;
  } catch (e) {
    log(`  ✗ Error browser: ${e.message}`);
    return null;
  }
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: 'Usage: node script.js <url> <work_dir>' }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });
  
  log('Memulai...');
  log('URL:', url);
  
  const surl = extractSurl(url);
  if (!surl) {
    console.log(JSON.stringify({ ok: false, error: 'Tidak bisa ekstrak surl' }));
    process.exit(1);
  }
  
  log('Surl:', surl);
  const urlObj = new URL(url);
  const baseUrl = `${urlObj.protocol}//${urlObj.hostname}`;
  
  const browser = await chromium.launch({
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
      throw new Error('Tidak dapat cookies');
    }
    
    fs.writeFileSync(path.join(workDir, 'terabox_cookies.json'), JSON.stringify(cookies));
    
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
    
    const allFiles = [{ server_filename: rootName, isdir: '1', size: '0', path: '/', isRoot: true }];
    
    for (const folder of rootFolders) {
      log(`\nTraverse: ${folder.server_filename || folder.name}`);
      const files = await traverseFolder(surl, cookies, baseUrl, folder.path, 0);
      allFiles.push({ server_filename: folder.server_filename || folder.name, isdir: '1', size: '0', path: folder.path, isFolderItem: true });
      allFiles.push(...files);
    }
    
    const filesOnly = allFiles.filter(i => String(i.isdir) !== '1');
    log(`\n=== Total: ${filesOnly.length} file ===`);
    
    log('\nMemulai resolve dlink (browser single session)...');
    const results = [];
    
    for (let i = 0; i < filesOnly.length; i++) {
      const item = filesOnly[i];
      log(`[${i+1}/${filesOnly.length}] ${item.server_filename || item.name}`);
      
      let dlink = await resolveDlinkInBrowser(page, item, surl, baseUrl);
      
      if (!dlink) {
        log(`  ! Gagal, skip`);
        results.push({
          filename: item.server_filename || item.name,
          size: formatSize(item.size),
          path: item.path,
          fsid: item.fs_id || item.fsid,
          dlink: null,
          status: 'failed',
        });
      } else {
        results.push({
          filename: item.server_filename || item.name,
          size: formatSize(item.size),
          path: item.path,
          fsid: item.fs_id || item.fsid,
          dlink: dlink,
          status: 'success',
        });
      }
    }
    
    const successCount = results.filter(r => r.status === 'success').length;
    const failCount = results.filter(r => r.status === 'failed').length;
    
    const output = {
      ok: true,
      type: 'single_browser',
      title: rootName,
      total: filesOnly.length,
      resolved: successCount,
      failed: failCount,
      cookie_file: path.join(workDir, 'terabox_cookies.json'),
      referer: `${baseUrl}/sharing/link?surl=${surl}`,
      results: results,
    };
    
    console.log(JSON.stringify(output));
    
    fs.writeFileSync(path.join(workDir, 'terabox_results.json'), JSON.stringify(results, null, 2));
    log(`\nSelesai: ${successCount} berhasil, ${failCount} gagal`);
    
  } catch (err) {
    log('ERROR:', err.message);
    console.log(JSON.stringify({ ok: false, error: String(err.message) }));
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main();
