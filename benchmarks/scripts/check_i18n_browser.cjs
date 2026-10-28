/* Focused file:// browser controls for report i18n. No provider/model calls. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const site = path.resolve(option('--site', 'benchmarks/site'));
const output = path.resolve(option('--output', '.cache/benchmark-runtime/report-i18n-browser'));
let playwright;
for (const name of [process.env.PLAYWRIGHT_MODULE, 'playwright', '/mnt/c/Users/mana/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'].filter(Boolean)) {
  try {playwright = require(name); break;} catch (_) {}
}
if (!playwright) throw new Error('Install Playwright or set PLAYWRIGHT_MODULE.');
const executablePath = process.env.CHROMIUM_EXECUTABLE ||
  (fs.existsSync('/home/mana/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome') ? '/home/mana/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome' : undefined);
(async () => {
  fs.mkdirSync(output, {recursive: true});
  const browser = await playwright.chromium.launch({headless: true, executablePath, args: ['--no-sandbox']});
  const failures = [], network = [], checks = [];
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'en-US'});
    const page = await context.newPage();
    page.on('pageerror', error => failures.push(error.message));
    page.on('request', request => {if (/^https?:/.test(request.url())) network.push(request.url());});
    const open = async (file, language = 'en') => {
      await page.goto(pathToFileURL(path.join(site, file)).href + '?lang=' + language);
      await page.waitForFunction(() => window.REA_I18N && document.documentElement.dataset.i18nWarnings !== undefined);
    };
    const settle = () => page.waitForTimeout(60);
    const noWarnings = async () => assert.deepEqual(await page.evaluate(() => window.REA_I18N.warnings), []);
    const files = ['index.html','dimensions.html','comparison.html','cases/index.html','capabilities.html','costs.html','coverage.html',
      'compilers.html','diagnostics.html','measurement.html','methodology.html','history/index.html','capability-inventory.html'];
    if (fs.existsSync(path.join(site, 'attempts'))) files.push(...fs.readdirSync(path.join(site, 'attempts')).filter(f => f.endsWith('.html')).slice(0, 2).map(f => 'attempts/' + f));
    for (const file of files) {
      await open(file);
      const evidence = await page.locator('code,pre').allTextContents();
      const geometry = await page.locator('[data-radar-series] circle').evaluateAll(nodes => nodes.map(n => [n.getAttribute('cx'), n.getAttribute('cy')]));
      for (const language of ['en', 'zh-CN', 'ja']) {
        await page.selectOption('#report-language', language); await settle();
        assert.equal(await page.locator('html').getAttribute('lang'), language);
        await noWarnings();
        assert.deepEqual(await page.locator('code,pre').allTextContents(), evidence);
        assert.deepEqual(await page.locator('[data-radar-series] circle').evaluateAll(nodes => nodes.map(n => [n.getAttribute('cx'), n.getAttribute('cy')])), geometry);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false, file + ': horizontal overflow');
        checks.push({file, language, warnings: 0});
      }
    }
    await open('index.html', 'ja');
    assert.match(await page.locator('h1').innerText(), /全体評価/);
    await page.selectOption('#report-language', 'en'); await settle();
    assert.match(await page.locator('h1').innerText(), /Global benchmark/);
    assert.doesNotMatch(await page.locator('h1').innerText(), /[\u4e00-\u9fff]/);
    await page.selectOption('#report-language', 'zh-CN'); await settle();
    assert.match(await page.locator('h1').innerText(), /全量测评/);
    const nav = page.locator('nav a').filter({hasText: '能力图表'}).first();
    assert.equal(new URL(await nav.getAttribute('href')).searchParams.get('lang'), 'zh-CN');
    await nav.click(); await page.waitForLoadState('load');
    await page.waitForFunction(() => window.REA_I18N?.locale === 'zh-CN'); await settle();
    assert.equal(await page.locator('html').getAttribute('lang'), 'zh-CN');

    // Table labels localize; option values and selected evidence stay canonical.
    await open('cases/index.html', 'zh-CN');
    const filter = page.locator('[data-table-filter]').first();
    await filter.fill('___no_result___'); await settle();
    assert.match(await page.locator('.filter-empty:visible').first().innerText(), /没有匹配行/);
    const queryBefore = new URL(page.url()).search;
    await page.selectOption('#report-language', 'ja'); await settle();
    assert.equal(await filter.inputValue(), '___no_result___');
    assert.match(await page.locator('.filter-empty:visible').first().innerText(), /一致する行がありません/);
    assert.ok(new URL(page.url()).search.includes('___no_result___'));
    assert.ok(queryBefore.includes('___no_result___'));
    await page.locator('.filter-controls button').first().click(); await settle();
    const select = page.locator('.filter-controls select').first();
    const canonical = await select.locator('option').nth(1).getAttribute('value');
    await select.selectOption(canonical); await settle();
    await page.selectOption('#report-language', 'en'); await settle();
    assert.equal(await select.inputValue(), canonical);
    await noWarnings();
    checks.push({control: 'filter state, localized empty status, canonical options and URL state'});

    // Dynamic radar messages/tooltips and selection survive language changes.
    await open('index.html', 'zh-CN');
    const figure = page.locator('[data-radar-group]').first();
    await figure.locator('[data-radar-none]').click(); await settle();
    assert.match(await figure.locator('[data-radar-empty]').innerText(), /未选择模型/);
    await page.selectOption('#report-language', 'ja'); await settle();
    assert.equal(await figure.locator('[data-radar-toggle]:checked').count(), 0);
    assert.match(await figure.locator('[data-radar-status]').innerText(), /モデルを表示/);
    await figure.locator('[data-radar-all]').click(); await settle();
    await figure.locator('[data-radar-tip]').first().focus(); await settle();
    assert.doesNotMatch(await figure.locator('[data-radar-readout]').innerText(), /Hover or focus/);
    await page.evaluate(() => window.dispatchEvent(new Event('beforeprint'))); await settle();
    await noWarnings();
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    checks.push({control: 'radar selection, translated tooltip and print scope'});

    for (const language of ['en', 'zh-CN', 'ja']) {
      await page.setViewportSize({width: 390, height: 844}); await open('index.html', language); await settle();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false, language + ': mobile overflow');
      await page.screenshot({path: path.join(output, language + '-mobile.png'), fullPage: false});
      await noWarnings(); checks.push({control: 'mobile', language});
    }
    await page.setViewportSize({width: 1440, height: 1000});
    await open('index.html', 'ja');
    await page.screenshot({path: path.join(output, 'ja-desktop.png'), fullPage: false});

    // Fault injection is confined to this page's in-memory dictionaries.
    const warningLogs = [];
    page.on('console', message => {if (message.type() === 'warning' && message.text().includes('[REA i18n]')) warningLogs.push(message.text());});
    await page.evaluate(() => {delete window.REA_TRANSLATIONS['zh-CN'].Overview;window.REA_I18N.changeLanguage('zh-CN');}); await settle();
    assert.ok(await page.locator('.i18n-warning').isVisible());
    assert.ok((await page.evaluate(() => window.REA_I18N.warnings)).some(w => w.key === 'Overview' && w.reason === 'missing_translation'));
    const logsBefore = warningLogs.filter(s => s.includes('missing_translation:Overview')).length;
    await page.evaluate(() => window.REA_I18N.changeLanguage('zh-CN')); await settle();
    assert.equal(warningLogs.filter(s => s.includes('missing_translation:Overview')).length, logsBefore);
    assert.equal(logsBefore, 1);
    const fallback = await page.evaluate(() => {
      window.REA_TRANSLATIONS['zh-CN']['Page {0} of {1}'] = '{0}/{9}';
      return window.REA_I18N.translate('Page 2 of 5');
    });
    assert.equal(fallback, 'Page 2 of 5');
    assert.ok((await page.evaluate(() => window.REA_I18N.warnings)).some(w => w.reason === 'placeholder_mismatch'));
    await page.evaluate(() => {
      window.REA_TRANSLATIONS['zh-CN'].Overview = '<img id="i18n-injected" src="x">';
      window.REA_I18N.changeLanguage('zh-CN');
      const node = document.createElement('p');node.textContent = 'Unregistered interface message';document.body.append(node);
    }); await settle();
    assert.equal(await page.locator('#i18n-injected').count(), 0);
    assert.ok((await page.evaluate(() => window.REA_I18N.warnings)).some(w => w.reason === 'missing_source' && w.key === 'Unregistered interface message'));
    await page.evaluate(() => {delete window.REA_TRANSLATIONS.ja;window.REA_I18N.changeLanguage('ja');}); await settle();
    assert.ok((await page.evaluate(() => window.REA_I18N.warnings)).some(w => w.reason === 'missing_language_file'));
    assert.match(await page.locator('h1').innerText(), /Global benchmark/);
    checks.push({control: 'missing key/file, invalid placeholders, warning deduplication and text-only insertion'});
    assert.deepEqual(failures, []); assert.deepEqual(network, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({passed: true, checks, javascript_errors: failures, outbound_requests: network}, null, 2));
    console.log(`PASS: ${checks.length} browser controls; 0 JavaScript errors; 0 outbound requests.`);
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode = 1;});
