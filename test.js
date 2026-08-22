const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.goto('https://example.com');
  console.log('SUKSES:', await page.title());
  await browser.close();
})();
