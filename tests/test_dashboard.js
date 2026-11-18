// Headless check of dashboard.html: no JS errors, every section renders, filters change content.
// Run: node tests/test_dashboard.js   (screenshots -> screenshots/)
const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');
(async () => {
  const root = path.resolve(__dirname, '..');
  fs.mkdirSync(path.join(root, 'screenshots'), { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto('file://' + path.join(root, 'dashboard.html'));
  await page.waitForTimeout(2500);
  const res = {};
  const charts = await page.$$eval('.chart', els => els.map(e => [e.id, !!e.querySelector('.main-svg')]));
  res.charts_missing = charts.filter(c => !c[1]).map(c => c[0]);
  const txt = async sel => (await page.$eval(sel, e => e.innerText)).slice(0, 4000);
  const b10_full = await txt('#t-bottom10'), k_full = await txt('#kpis');
  // date filter
  await page.click('[data-preset="test"]'); await page.waitForTimeout(1200);
  const b10_test = await txt('#t-bottom10'), k_test = await txt('#kpis');
  res.date_filter_changes_kpis = k_full !== k_test;
  res.date_filter_changes_bottom10 = b10_full !== b10_test;
  res.range_info = await txt('#rangeinfo');
  const dlen = await page.$eval('#c-down', e => e.data[0].x.length);
  res.downtime_days_in_test_range = dlen;
  // custom date input
  await page.fill('#f-from', '2025-03-03'); await page.dispatchEvent('#f-from', 'change');
  await page.fill('#f-to', '2025-04-06'); await page.dispatchEvent('#f-to', 'change'); await page.waitForTimeout(1000);
  res.custom_range_info = await txt('#rangeinfo');
  await page.click('[data-preset="all"]'); await page.waitForTimeout(800);
  // bank filter
  await page.selectOption('#f-bank', 'B-31'); await page.waitForTimeout(1200);
  res.bank_title = await txt('#bank-title');
  res.bank_table_rows = await page.$$eval('#t-bank tbody tr', r => r.length);
  res.bank_chart_traces = await page.$eval('#c-bank', e => e.data.length);
  // guest group
  const before = await txt('#t-guest');
  await page.selectOption('#f-group', 'home_region|Local'); await page.waitForTimeout(800);
  res.guest_title = await txt('#guest-title');
  res.guest_changes = before !== (await txt('#t-guest'));
  // evidence links
  res.evidence_links = await page.$$eval('#plan-cards a', as => as.map(a => a.getAttribute('href')));
  res.evidence_targets_exist = await page.$$eval('#plan-cards a', as => as.every(a => !!document.querySelector(a.getAttribute('href'))));
  await page.selectOption('#f-bank', ''); await page.selectOption('#f-group', 'all|All'); await page.waitForTimeout(800);
  // screenshots per section
  const secs = await page.$$eval('section', s => s.map(x => x.id));
  for (const id of secs) { const el = await page.$('#' + id); await el.screenshot({ path: path.join(root, 'screenshots', id + '.png') }); }
  await page.setViewportSize({ width: 390, height: 800 });
  await page.waitForTimeout(800);
  res.mobile_overflow_px = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await page.screenshot({ path: path.join(root, 'screenshots', 'mobile_top.png') });
  res.errors = errors;
  console.log(JSON.stringify(res, null, 1));
  await browser.close();
  process.exit(errors.length || res.charts_missing.length ? 1 : 0);
})();
