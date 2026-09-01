/**
 * Terabox File Lister - Traverse folder & list all files
 * 
 * Fungsi:
 * 1. Buka browser, dapat cookies session
 * 2. Traverse folder rekursif, dapat daftar semua file
 * 3. Kirim daftar file ke status/channel Telegram
 * 
 * TIDAK melakukan: resolve dlink, download, upload
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CONFIG = {
  UA: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  BROWSER_TIMEOUT: 30000,
  POST_NAV_DELAY: 5000,
  COOKIE_RETRY_DELAY: 5000,
  API_TIMEOUT: 30000,
};

function log(...args) {
  console.error('[lister]', new Date().toISOString(), ...args);
}

function extractSurl(url) {
  let m = url.match(/[?&]surl=([^&]+)/);
  if (m) return decodeURIComponent(m[1]);
  m = url.match(/\/s\/([^/?]+)/);
  if (m) return m[1];
  m = url.match(/\/([a-zA-Z0-9_-]{6,32})\/?$/);
  return m ? m[1] : null;
}

function cookieHeader(cookies) {
  return cookies.map(c => `${c.name}=${c.value}`).join('; ');
}

async function fetchWithHeaders(url, cookies, referer, extra = {}) {
  const headers = {
    'User-Agent': CONFIG.UA,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': referer || url,
    'Cookie': cookieHeader(cookies),
    'Connection': 'keep-alive',
    ...extra,
  };
  
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CONFIG.API_TIMEOUT);
  
  try {
    const response = await fetch(url, { headers, signal: controller.signal });
    return {
      status: response.status,
      headers: Object.fromEntries(response.headers.entries()),
      body: await response.text(),
    };
  } finally {
    clearTimeout(timeout);
  }
}

function parseApiResponse(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function buildListParams(surl, opts = {}) {
  return new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    ...opts,
  });
}

/**
 * Format size ke human-readable
 */
function formatSize(bytes) {
  if (!bytes || bytes === '0') return '0 B';
  const num = parseInt(bytes, 10);
  if (num < 1024) return `${num} B`;
  if (num < 1024 * 1024) return `${(num / 1024).toFixed(1)} KB`;
  if (num < 1024 * 1024 * 1024) return `${(num / (1024*1024)).toFixed(1)} MB`;
  return `${(num / (1024*1024*1024)).toFixed(2)} GB`;
}

/**
 * Format nama file - handle character encoding
 */
function formatFilename(name) {
  if (!name) return 'unknown';
  // Potong jika terlalu panjang
  if (name.length > 80) {
    return name.slice(0, 77) + '...';
  }
  return name;
}

/**
 * Gabungkan semua file dari traverse ke daftar yang rapi
 */
function formatFileList(files, folderName) {
  const lines = [];
  
  lines.push(`📁 Folder: ${folderName || 'Unknown'}`);
  lines.push(`📄 Total file: ${files.length}`);
  lines.push(`\\n--- Daftar File ---`);
  
  // Sort: folder dulu, lalu file, lalu sort nama
  const sorted = [...files].sort((a, b) => {
    const aIsDir = String(a.isdir) === '1';
    const bIsDir = String(b.isdir) === '1';
    if (aIsDir && !bIsDir) return -1;
    if (!aIsDir && bIsDir) return 1;
    return (a.server_filename || a.name || '').localeCompare(b.server_filename || b.name || '');
  });
  
  for (const item of sorted) {
    const isDir = String(item.isdir) === '1';
    const name = formatFilename(item.server_filename || item.name || 'unknown');
    const size = isDir ? '—' : formatSize(item.size);
    const path = item.path || '';
    
    const icon = isDir ? '📂' : '📄';
    lines.push(`${icon} ${name} (${size})${path ? ` [${path}]` : ''}`);
  }
  
  return lines.join('\\n');
}

/**
 * Traverse folder rekursif - dapat semua file
 */
async function traverseAllFiles(surl, cookies, baseUrl, folderPath, depth = 0) {
  if (depth > 10) {
    log(`Melebihi depth maks (${depth}) pada ${folderPath}`);
    return [];
  }
  
  const folderName = folderPath || '/';
  log(`Traverse folder: ${folderName} (depth ${depth})`);
  
  const listParams = buildListParams(surl, { root: '0', dir: folderPath || '/' });
  const result = await fetchWithHeaders(
    `https://www.terabox.com/share/list?${listParams}`,
    cookies,
    `${baseUrl}/sharing/link?surl=${surl}`
  );
  
  const data = parseApiResponse(result.body);
  if (!data || data.errno !== 0 || !data.list) {
    log(`Gagal traverse ${folderName}: errno=${data?.errno || 'unknown'}`);
    return [];
  }
  
  const allFiles = [];
  
  for (const item of data.list) {
    if (String(item.isdir) === '1') {
      // Folder: recurse
      log(`  Folder: ${item.server_filename || item.name} -> recurse`);
      const subFiles = await traverseAllFiles(surl, cookies, baseUrl, item.path, depth + 1);
      allFiles.push(...subFiles);
      // Juga tambahkan folder itu sendiri sebagai item (untuk list)
      allFiles.push({
        ...item,
        isFolderItem: true,
        fullPath: item.path,
      });
    } else {
      // File: kumpulkan
      allFiles.push({
        ...item,
        fullPath: item.path,
        parentPath: folderPath,
      });
      log(`  File: ${item.server_filename || item.name} (${item.size || 0}b)`);
    }
  }
  
  return allFiles;
}

/**
 * Dapatkan daftar folder di root (untuk traverse)
 */
async function getRootFolders(surl, cookies, baseUrl) {
  const listParams = buildListParams(surl, { root: '1' });
  const result = await fetchWithHeaders(
    `https://www.terabox.com/share/list?${listParams}`,
    cookies,
    `${baseUrl}/sharing/link?surl=${surl}`
  );
  
  const data = parseApiResponse(result.body);
  if (!data || data.errno !== 0 || !data.list) {
    return { folders: [], error: `API error: ${data?.errno || 'unknown'}` };
  }
  
  const folders = data.list.filter(item => String(item.isdir) === '1');
  const rootName = data.title || 'Share Link';
  
  return { folders, rootName, error: null };
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ 
      ok: false, 
      error: 'Usage: node terabox_lister.js <url> <work_dir>' 
    }));
    process.exit(1);
  }
  
  if (!fs.existsSync(workDir)) {
    fs.mkdirSync(workDir, { recursive: true });
  }
  
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
  
  try {
    // Buka browser untuk dapat cookies
    log('Membuka browser untuk dapat session...');
    browser = await chromium.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-blink-features=AutomationControlled',
      ],
    });
    
    const context = await browser.newContext({
      userAgent: CONFIG.UA,
      locale: 'en-US',
      viewport: { width: 1920, height: 1080 },
    });
    
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
      Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    });
    
    const page = await context.newPage();
    page.on('dialog', async (d) => d.dismiss().catch(() => {}));
    
    // Navigasi ke halaman
    log('Navigasi ke halaman share...');
    await page.goto(url, { 
      waitUntil: 'domcontentloaded', 
      timeout: CONFIG.BROWSER_TIMEOUT 
    }).catch(e => log('Navigate warning:', e.message));
    
    await page.waitForTimeout(CONFIG.POST_NAV_DELAY);
    
    // Dapatkan cookies
    let cookies = await context.cookies();
    log(`Dapat ${cookies.length} cookies`);
    
    if (cookies.length === 0) {
      log('Tidak ada cookies, retry...');
      await page.waitForTimeout(CONFIG.COOKIE_RETRY_DELAY);
      cookies = await context.cookies();
      log(`Setelah retry: ${cookies.length} cookies`);
    }
    
    if (cookies.length === 0) {
      throw new Error('Tidak dapat session cookies. IP mungkin terblokir atau server bermasalah.');
    }
    
    // Simpan cookies
    const cookieFile = path.join(workDir, 'terabox_cookies.json');
    fs.writeFileSync(cookieFile, JSON.stringify(cookies));
    log('Cookies disimpan ke:', cookieFile);
    
    // Tutup browser setelah dapat cookies
    await browser.close();
    browser = null;
    
    // Dapatkan info dari root
    log('Mendapatkan daftar dari root...');
    const rootResult = await getRootFolders(surl, cookies, baseUrl);
    
    if (rootResult.error) {
      console.log(JSON.stringify({
        ok: false,
        error: rootResult.error,
        debug: { url, surl, baseUrl }
      }));
      process.exit(1);
    }
    
    const { folders, rootName } = rootResult;
    log(`Root: ${rootName}`);
    log(`Folder di root: ${folders.length}`);
    
    // Traverse semua folder dan kumpulkan file
    log('Memulai traverse folder...');
    const allFiles = [];
    
    // Tambahkan folder root sebagai item
    allFiles.push({
      server_filename: rootName,
      isdir: '1',
      size: '0',
      path: '/',
      isRoot: true,
    });
    
    for (const folder of folders) {
      const folderPath = folder.path;
      const folderName = folder.server_filename || folder.name || 'Unknown';
      
      log(`\\nTraverse folder: ${folderName}`);
      const files = await traverseAllFiles(surl, cookies, baseUrl, folderPath, 0);
      
      // Tambahkan folder itu sendiri
      allFiles.push({
        server_filename: folderName,
        isdir: '1',
        size: '0',
        path: folderPath,
        isFolderItem: true,
      });
      
      // Tambahkan semua file dari folder ini
      allFiles.push(...files);
    }
    
    // Filter hanya file (jangan folder)
    const filesOnly = allFiles.filter(item => String(item.isdir) !== '1');
    const foldersOnly = allFiles.filter(item => String(item.isdir) === '1');
    
    log(`\\n=== Hasil Traverse ===`);
    log(`Total folder: ${foldersOnly.length}`);
    log(`Total file: ${filesOnly.length}`);
    
    // Format daftar untuk dikirim ke Telegram
    const fileListText = formatFileList(filesOnly, rootName);
    
    // Buat output JSON yang bisa diproses oleh handler Telegram
    const output = {
      ok: true,
      type: 'file_list',
      title: rootName,
      totalFolders: foldersOnly.length,
      totalFiles: filesOnly.length,
      cookie_file: cookieFile,
      referer: `${baseUrl}/sharing/link?surl=${surl}`,
      file_list: filesOnly.map(f => ({
        filename: f.server_filename || f.name || 'unknown',
        size: f.size ? formatSize(f.size) : '—',
        path: f.path || '',
        fsid: f.fs_id || f.fsid || '',
      })),
      // Format teks untuk dikirim ke Telegram (plain text)
      text_for_telegram: fileListText,
      // Format JSON untuk processing lebih lanjut
      json_for_telegram: JSON.stringify({
        title: rootName,
        total: filesOnly.length,
        files: filesOnly.map(f => ({
          name: f.server_filename || f.name || 'unknown',
          size: f.size ? formatSize(f.size) : '—',
          path: f.path || '',
        }))
      }, null, 2),
    };
    
    console.log(JSON.stringify(output, null, 2));
    
    // Simpan file list ke file terpisah untuk reference
    fs.writeFileSync(
      path.join(workDir, 'file_list.json'),
      JSON.stringify(output.file_list, null, 2)
    );
    fs.writeFileSync(
      path.join(workDir, 'file_list_text.txt'),
      fileListText
    );
    
    log(`\\nFile list disimpan ke: ${workDir}`);
    
  } catch (err) {
    log('ERROR:', err.message);
    if (browser) {
      try { await browser.close(); } catch {}
    }
    console.log(JSON.stringify({ 
      ok: false, 
      error: String(err.message || err) 
    }));
    process.exit(1);
  } finally {
    if (browser) {
      try { await browser.close(); } catch {}
    }
  }
}

main();
