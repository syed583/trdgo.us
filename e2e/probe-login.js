// Read-only: what the two sign-in pages actually render, so the suite stops guessing.
const { chromium } = require('@playwright/test');

(async () => {
  const base = process.env.E2E_BASE_URL || 'https://tradgo.us';
  const b = await chromium.launch();
  for (const path of ['/admin/login', '/login']) {
    const p = await b.newPage();
    try {
      await p.goto(base + path, { waitUntil: 'domcontentloaded' });
      await p.waitForTimeout(3000);
      const counts = {
        password: await p.locator('input[type="password"]').count(),
        tel: await p.locator('input[placeholder="Phone number"]').count(),
        anyInput: await p.locator('input').count(),
        adlBtn: await p.locator('button.adl-btn').count(),
        lpBtn: await p.locator('button.lp-btn').count(),
      };
      console.log(path, JSON.stringify(counts));
    } catch (e) {
      console.log(path, 'FAILED:', e.message.split('\n')[0]);
    }
    await p.close();
  }
  await b.close();
})();
