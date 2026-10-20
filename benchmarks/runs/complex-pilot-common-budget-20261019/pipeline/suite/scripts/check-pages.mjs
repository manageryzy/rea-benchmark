import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../../..', import.meta.url)).replace(/\/$/, '');
const report = `${root}/benchmarks/reports/${process.argv[2] ?? 'complex-pilot-common-budget-20261019'}`;
const baseUrl = process.env.BENCHMARK_REPORT_URL ?? 'http://127.0.0.1:8767';
await mkdir(`${root}/.cache/benchmark-browser/config`, { recursive: true });
await mkdir(`${root}/.cache/benchmark-browser/cache`, { recursive: true });
const browser = await chromium.launch({
  executablePath: '/home/mana/.cache/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell',
  env: { ...process.env, XDG_CONFIG_HOME: `${root}/.cache/benchmark-browser/config`, XDG_CACHE_HOME: `${root}/.cache/benchmark-browser/cache` },
  headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-crash-reporter'],
});
const errors = [];
const results = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', error => errors.push(error.message));
  for (const path of ['index.html', 'coverage.html', 'methodology.html', 'cases/cpp.html', 'cases/rust.html', 'cases/go.html']) {
    const response = await page.goto(`${baseUrl}/${path}`);
    assert.equal(response.status(), 200);
    assert.equal(await page.locator('h1').count(), 1);
    assert(await page.locator('main').innerText());
    for (const href of await page.locator('a').evaluateAll(links => links.map(link => link.getAttribute('href')).filter(Boolean))) {
      if (href.startsWith('https://') || href.startsWith('#')) continue;
      const target = new URL(href, page.url()).href;
      const checked = await page.request.get(target);
      assert.equal(checked.status(), 200, target);
    }
    results.push({ path, loaded: true, links_ok: true });
  }
  await page.goto(`${baseUrl}/index.html`);
  await page.selectOption('#language-filter', { label: 'Go' });
  assert.equal(await page.locator('#point-table tbody tr:visible').count(), 6);
  await page.selectOption('#language-filter', 'all');
  assert.equal(await page.locator('#point-table tbody tr:visible').count(), 18);
  await page.screenshot({ path: `${report}/preview-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${report}/preview-mobile.png`, fullPage: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Page overflows mobile viewport');
  assert.deepEqual(errors, []);
  await writeFile(`${report}/data/page-validation.json`, JSON.stringify({ passed: true, pages: results, filter_works: true, mobile_viewport: 390, script_errors: errors }, null, 2));
  console.log('PASS: six HTML pages, all local links, language filtering, desktop/mobile rendering and no script errors.');
} finally {
  await browser.close();
}
