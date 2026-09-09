
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

function surlFromUrl(url) {
  const m = url.match(/[?&]surl=([^&]+)/) || url.match(/\/s\/([^/?]+)/);
  return m ? m[1] : null;
}

async function apiGetCookies(url, surl) {
  // Kunjungi halaman share untuk dapat cookies session
  const response = await fetch(url, {
    headers: {
      'User-Agent': UA,
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9',
    },
  });
  
  // Parse cookies dari response headers
  const cookies = {};
  const setCookie = response.headers.get('set-cookie');
  if (setCookie) {
    for (const line of setCookie.split(', ')) {
      const [name, ...rest] = line.split('=');
      cookies[name.trim()] = rest.join('=').split(';')[0].trim();
    }
  }
  
  return { cookies, surl };
}

async function apiCall(apiPath, cookies, surl) {
  const params = new URLSearchParams({
    app_id: '250528',
    channel: 'chunlei',
    web: '1',
    clienttype: '0',
    shorturl: surl,
    root: '1',
  });
  
  const apiUrl = `https://www.terabox.com${apiPath}?${params}`;
  
  const response = await fetch(apiUrl, {
    headers: {
      'User-Agent': UA,
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'en-US,en;q=0.9',
      'Referer': `https://www.terabox.com/sharing/link?surl=${surl}`,
      'Cookie': Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; '),
    },
  });
  
  return {
    status: response.status,
    body: await response.text(),
    headers: Object.fromEntries(response.headers.entries()),
  };
}

async function main() {
  const url = process.argv[2];
  const workDir = process.argv[3];
  
  if (!url || !workDir) {
    console.log(JSON.stringify({ ok: false, error: 'Usage: node terabox_minimal.js <url> <work_dir>' }));
    process.exit(1);
  }
  
  if (!require('fs').existsSync(workDir)) {
    require('fs').mkdirSync(workDir, { recursive: true });
  }
  
  try {
    log('Membuka halaman untuk dapat cookies...');
    const { cookies, surl } = await apiGetCookies(url, surlFromUrl(url));
    log('Cookie count:', Object.keys(cookies).length);
    log('Surl:', surl);
    
    if (Object.keys(cookies).length === 0) {
      throw new Error('Tidak dapat cookie session dari Terabox');
    }
    
    log('Memanggil API share/list...');
    const apiResult = await apiCall('/share/list', cookies, surl);
    log('API status:', apiResult.status);
    
    let apiData;
    try {
      apiData = JSON.parse(apiResult.body);
    } catch (e) {
      throw new Error('Respons API tidak valid');
    }
    
    console.log('API data:', JSON.stringify(apiData).slice(0, 300));
    
    if (apiData.errno !== 0 && apiData.errno !== undefined) {
      const errorMap = {
        105: 'Link tidak ditemukan / expired',
        '-6': 'Butuh login (link privat)',
        '-7': 'Link tidak valid',
      };
      throw new Error(`Terabox API error ${apiData.errno}: ${errorMap[apiData.errno] || 'Unknown'}`);
    }
    
    if (!apiData.list || !Array.isArray(apiData.list)) {
      throw new Error('Respons API tidak menyertakan list file');
    }
    
    const list = apiData.list;
    console.log('Jumlah item:', list.length);
    
    // Handle single file
    if (list.length === 1 && String(list[0].isdir) !== '1') {
      const item = list[0];
      let dlink = item.dlink || item.dlink_url || item.download_url;
      
      if (!dlink) {
        // Try download endpoint
        const dlParams = new URLSearchParams({
          app_id: '250528',
          channel: 'chunlei',
          web: '1',
          clienttype: '0',
          shorturl: surl,
          fsid: String(item.fs_id || item.fsid),
          rt: '1',
          type: 'dlink',
        });
        const dlResult = await apiCall('/share/download', cookies, surl);
        try {
          const dlData = JSON.parse(dlResult.body);
          if (dlData.errno === 0 && dlData.dlink) {
            dlink = dlData.dlink;
          }
        } catch (e) {}
      }
      
      if (!dlink) {
        throw new Error('File single tidak bisa di-resolve');
      }
      
      const cookieFile = require('path').join(workDir, 'terabox_cookies.json');
      require('fs').writeFileSync(cookieFile, JSON.stringify(cookies));
      
      console.log(JSON.stringify({
        ok: true,
        type: 'single',
        direct_url: dlink,
        filename: item.server_filename || item.name || 'terabox_file',
        cookie_file: cookieFile,
        referer: `https://www.terabox.com/sharing/link?surl=${surl}`,
      }));
      return;
    }
    
    // Handle folder
    const files = [];
    for (const item of list) {
      if (String(item.isdir) !== '1') {
        const dlink = item.dlink || item.dlink_url || item.download_url;
        if (dlink) {
          files.push({ url: dlink, filename: item.server_filename || item.name });
        }
      }
    }
    
    if (files.length === 0) {
      throw new Error('Tidak ada file yang bisa di-resolve');
    }
    
    const cookieFile = require('path').join(workDir, 'terabox_cookies.json');
    require('fs').writeFileSync(cookieFile, JSON.stringify(cookies));
    
    console.log(JSON.stringify({
      ok: true,
      type: 'folder',
      files: files,
      cookie_file: cookieFile,
      referer: `https://www.terabox.com/sharing/link?surl=${surl}`,
    }));
    
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err.message || err) }));
    process.exitCode = 1;
  }
}

function log(...args) {
  console.error('[minimal]', new Date().toISOString(), ...args);
}

main();
