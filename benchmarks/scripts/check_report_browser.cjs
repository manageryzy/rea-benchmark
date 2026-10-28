#!/usr/bin/env node
'use strict';
/* Offline reader-visible acceptance against already-generated reports.
 * No report generation, target execution, model calls, or external requests.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');
const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf(name);
  return at < 0 ? fallback : args[at + 1];
};
const site = path.resolve(root, option('--site', 'benchmarks/site'));
const localSite = path.resolve(root, option('--local', 'benchmarks/reports'));
const output = path.resolve(root, option('--output', '.cache/benchmark-runtime/report-browser'));
const only = option('--only', '');
const playwrightPaths = [process.env.PLAYWRIGHT_MODULE, 'playwright',
  '/mnt/c/Users/mana/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'].filter(Boolean);
let playwright;
for (const name of playwrightPaths) {
  try { playwright = require(name); break; } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
  }
}
if (!playwright) throw new Error('Install Playwright or set PLAYWRIGHT_MODULE to its module path.');
const executablePath = process.env.CHROMIUM_EXECUTABLE ||
  (fs.existsSync('/home/mana/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome')
    ? '/home/mana/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome' : undefined);
const url = name => pathToFileURL(path.join(site, name)).href;

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await playwright.chromium.launch({ headless: true, executablePath,
    args: ['--no-sandbox'] });
  const browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  let blockedRequests = 0;
  await context.route(/https?:\/\//, route => { blockedRequests++; return route.abort(); });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const results = [];
  async function check(name, body) {
    if (only && !name.toLowerCase().includes(only.toLowerCase())) return;
    const started = performance.now();
    try {
      const evidence = await body();
      results.push({ name, passed: true, seconds: (performance.now() - started) / 1000, evidence });
      console.log('PASS ' + name);
    } catch (error) {
      results.push({ name, passed: false, seconds: (performance.now() - started) / 1000,
        error: error.message });
      console.log('FAIL ' + name + ': ' + error.message);
    }
  }
  const visibleRows = () => page.locator('#all-cases tbody tr:not([hidden])').allTextContents();
  try {
    await check('aggregate overview radar, counts and responsive layout', async () => {
      const initialErrors = pageErrors.length, initialRequests = blockedRequests;
      await page.goto(url('index.html'));
      const radar = page.locator('[data-radar-group="capability-overview"]');
      assert(await radar.isVisible(), 'Overview radar is missing or collapsed');
      const toggles = radar.locator('[data-radar-toggle]');
      assert.equal(await toggles.count(), 6);
      assert.equal(await page.locator('[data-cohort-selection]').count(), 0, 'Native comparison displaced aggregate overview');
      const modelRows = page.locator('#coverage > .scroll tbody tr');
      assert.equal(await modelRows.count(), 6);
      const totals = await modelRows.evaluateAll(rows => rows.reduce((sum, row) => {
        const fields = [...row.children].map(c => c.textContent.trim());
        const [graded, planned] = fields[1].split('/').map(Number);
        const passed = Number(fields[2]), partial = Number(fields[3]), ungraded = Number(fields[4]);
        if (passed + partial !== graded || graded + ungraded !== planned) throw Error('Coverage does not conserve cells');
        return { graded: sum.graded + graded, planned: sum.planned + planned };
      }, {graded: 0, planned: 0}));
      assert.equal(totals.planned, 1170);
      assert((await page.locator('#status').innerText()).includes(`${totals.graded}/${totals.planned}`));
      assert((await page.locator('.category-cards').innerText()).includes('待测 · Unmeasured'));
      const labels = await page.locator('[data-dimension-group="capability-overview"] .dimension-matrix thead th').allTextContents();
      assert.equal(labels.length, 5, 'Expected four measured capability families');
      assert(labels.some(s => s.includes('基础语言能力')) && labels.some(s => s.includes('类型还原能力')));
      assert(!labels.some(s => s.includes('脱壳')), 'Planned unpacking was invented as a measured axis');
      const polygons = await radar.locator('.dimension-profile').count();
      assert(polygons >= 2, 'Measured model profiles are not connected');
      const first = await toggles.first().getAttribute('value');
      await toggles.first().uncheck();
      assert(await radar.locator(`[data-radar-series="${first}"]`).evaluate(n => n.hasAttribute('hidden')));
      await page.reload();
      assert(!(await toggles.first().isChecked()), 'Model selection was not preserved');
      await radar.locator('[data-radar-none]').click();
      assert(await radar.locator('[data-radar-empty]').isVisible());
      await radar.locator('[data-radar-all]').click();
      await radar.locator('[data-radar-tip]').first().focus();
      assert((await radar.locator('[data-radar-readout]').innerText()).includes('n='));
      const layouts = [];
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height: 1000});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Page overflows viewport');
        const bounds = await radar.boundingBox();
        assert(bounds.width <= width, 'Radar overflows viewport');
        await page.screenshot({path: path.join(output, `aggregate-overview-${width}.png`), fullPage: true});
        layouts.push({width, radarWidth: bounds.width});
      }
      await page.goto(url('capabilities.html'));
      assert((await page.locator('#status').innerText()).includes('All valid saved grades count once'));
      assert.equal(pageErrors.length, initialErrors, 'Overview has JavaScript errors');
      assert.equal(blockedRequests, initialRequests, 'Offline report attempted external requests');
      return {totals, polygons, layouts, modelSelectionRestored: true};
    });
    await check('filter history, shared URL, empty result and clear', async () => {
      await page.goto(url('cases/index.html'));
      const input = page.locator('input[data-table-filter="all-cases"]');
      await input.fill('GLM');
      const before = await visibleRows();
      assert(before.length > 0, 'GLM has no visible saved results');
      assert(before.every(text => /glm/i.test(text)), 'Filter leaves another model visible');
      const savedURL = page.url();
      assert(new URL(savedURL).searchParams.has('filter-all-cases'), 'Query is absent from shareable URL');
      const link = page.locator('#all-cases tbody tr:not([hidden]) a[href*=".html"]').first();
      const href = await link.getAttribute('href');
      assert(href.includes('#'), 'Result table discards model anchor');
      await link.click();
      const fragment = decodeURIComponent(new URL(page.url()).hash.slice(1));
      assert(await page.locator('[id]').evaluateAll((nodes, id) => nodes.some(node => node.id === id), fragment),
        'Result model anchor does not exist');
      await page.goBack();
      assert.equal(await input.inputValue(), 'GLM');
      assert.deepEqual(await visibleRows(), before, 'Back restores controls without matching results');
      await page.reload();
      assert.deepEqual(await visibleRows(), before, 'Reload changes visible filtered population');
      await page.goto(savedURL);
      assert.deepEqual(await visibleRows(), before, 'Shared URL changes visible population');
      await page.evaluate(() => {
        const clean = new URL(location.href);
        [...clean.searchParams.keys()].filter(key => key.startsWith('filter-')).forEach(key => clean.searchParams.delete(key));
        history.pushState(null, '', clean.href);
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      assert.equal(await input.inputValue(), '', 'Back to an unfiltered URL keeps a stale filter');
      await input.fill('no-such-result-rea-915470');
      assert.equal((await visibleRows()).length, 0);
      const status = page.locator('.filter-status[role="status"]').filter({ hasText: /0/ }).first();
      assert(await status.isVisible(), 'Empty filter has no visible live result count');
      await page.getByRole('button', { name: 'Clear filters', exact: true }).first().click();
      assert.equal(await input.inputValue(), '');
      const cleared = (await visibleRows()).length;
      assert(cleared >= before.length, 'Clear does not restore the result population');
      const modelSelect = page.locator('.filter-controls label').filter({ hasText: /^Model / }).locator('select');
      assert(await modelSelect.count(), 'Structured model filter is absent');
      const glmOption = await modelSelect.locator('option').evaluateAll(options => options.find(option => /glm/i.test(option.textContent))?.value);
      assert(glmOption, 'Structured model filter omits GLM');
      await modelSelect.selectOption(glmOption);
      await input.fill('cpp');
      const combined = await visibleRows();
      assert(combined.length && combined.every(text => /glm/i.test(text) && /cpp/i.test(text)),
        'Combined model and case filters disagree with visible rows');
      assert(new URL(page.url()).searchParams.has('filter-all-cases-model'));
      return { filteredRows: before.length, clearedRows: cleared, combinedRows: combined.length, destination: href };
    });
    await check('scope URL and keyboard restoration', async () => {
      await page.goto(url('comparison.html'));
      const activeCohort = page.locator('[data-cohort-panel]:not([hidden])');
      const button = activeCohort.locator('[data-chart-scope="all"]');
      await button.focus();
      await page.keyboard.press('Space');
      assert.equal(await button.getAttribute('aria-pressed'), 'true');
      assert.equal(new URL(page.url()).searchParams.get('scope'), 'all');
      assert(await activeCohort.locator('[data-chart-panel="all"]').isVisible());
      await page.reload();
      assert.equal(await button.getAttribute('aria-pressed'), 'true');
      assert(await activeCohort.locator('[data-chart-panel="all"]').isVisible());
      const cohort = page.locator('[data-cohort-selection]');
      let cohortKey = null;
      if (await cohort.count()) {
        cohortKey = await cohort.locator('option').last().getAttribute('value');
        await cohort.selectOption(cohortKey);
        assert.equal(new URL(page.url()).searchParams.get('cohort'), cohortKey);
        await page.reload();
        assert.equal(await cohort.inputValue(), cohortKey);
        const selected = await page.locator('[data-cohort-panel]').evaluateAll(nodes => nodes.filter(node => !node.hidden).map(node => node.dataset.cohortPanel));
        assert.deepEqual(selected, [cohortKey], 'Selected cohort panel does not match the URL');
      }
      return { scope: 'all', cohort: cohortKey };
    });
    await check('capability dimension profiles, cohorts and missing evidence', async () => {
      const initialErrors = pageErrors.length, initialRequests = blockedRequests;
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(url('dimensions.html'));
      const selector = page.locator('[data-cohort-selection]');
      const defaultCohort = await selector.inputValue();
      const activeCohort = () => page.locator('[data-cohort-panel]:not([hidden])');
      const activeScope = () => activeCohort().locator('[data-chart-panel]:not([hidden])');
      assert.equal(defaultCohort, 'maximum-coverage', 'Dimension page does not default to the available shared evidence');
      const language = activeScope().locator('[data-dimension-group="language"]');
      assert(await language.isVisible(), 'Default source-language matrix is not visible');
      assert(await language.evaluate(node => node.open), 'First dimension group starts collapsed');
      assert(await language.locator('.dimension-radars').evaluate(node => node.open), 'Language diagrams start hidden');
      const languages = await language.locator('thead th').allTextContents();
      assert.deepEqual(languages.slice(1, 4), ['C++', 'Rust', 'Go']);
      const defaultMeasured = await language.locator('.dimension-measured').count();
      assert(defaultMeasured > 0, 'Default profile has no measured language score');
      const evidence = { defaultCohort, defaultMeasured, layouts: [] };
      assert.equal(await language.locator('.dimension-radar .radar-plot > svg').count(), 1, 'Models are still split into separate radars');
      const radar = language.locator('[data-radar-group="language"]');
      const toggles = radar.locator('[data-radar-toggle]');
      assert(await toggles.count() > 1, 'Comparison needs multiple model toggles');
      const firstModel = await toggles.first().inputValue();
      const selection = () => radar.locator('[data-radar-series]').evaluateAll(nodes => nodes.map(node => ({
        model: node.dataset.radarSeries, hidden: node.hasAttribute('hidden'), aria: node.getAttribute('aria-hidden'),
        display: getComputedStyle(node).display,
      })));
      await toggles.first().uncheck();
      const oneOff = await selection();
      assert(oneOff.find(row => row.model === firstModel)?.hidden, 'Unchecked model remains drawn');
      assert(oneOff.filter(row => row.model !== firstModel).every(row => !row.hidden), 'Toggling one model hides other models');
      assert(oneOff.filter(row => row.hidden).every(row => row.aria === 'true' && row.display === 'none'), 'Hidden model remains visible or accessible');
      assert(!new URL(page.url()).searchParams.get('radar-language').split(',').includes(firstModel));
      const savedRadarURL = page.url();
      await page.reload();
      assert.deepEqual(await selection(), oneOff, 'Reload changes selected radar models');
      await page.goto(url('index.html'));
      await page.goBack();
      assert.equal(page.url(), savedRadarURL);
      assert.deepEqual(await selection(), oneOff, 'Back changes selected radar models');
      await toggles.first().focus();
      await page.keyboard.press('Space');
      assert(await toggles.first().isChecked(), 'Keyboard does not select the model');
      assert((await selection()).every(row => !row.hidden), 'Keyboard model toggle leaves stale series visibility');
      const point = radar.locator('[data-radar-series]:not([hidden]) [data-radar-tip]').first();
      await point.focus();
      assert.equal(await radar.locator('[data-radar-readout]').innerText(), await point.getAttribute('data-radar-tip'),
        'Keyboard point focus does not expose exact value and evidence');
      const hoverPoint = radar.locator('[data-radar-series]:not([hidden]) [data-radar-tip]').last();
      await hoverPoint.hover();
      assert.equal(await radar.locator('[data-radar-readout]').innerText(), await hoverPoint.getAttribute('data-radar-tip'),
        'Hover does not expose exact value and evidence');
      await radar.locator('[data-radar-none]').click();
      assert((await selection()).every(row => row.hidden), 'Hide all leaves visible model marks');
      assert.equal(new URL(page.url()).searchParams.get('radar-language'), '', 'Empty selection is not shareable');
      await page.reload();
      assert((await selection()).every(row => row.hidden), 'Reload treats an empty selection as all models');
      await radar.locator('[data-radar-all]').click();
      assert((await selection()).every(row => !row.hidden), 'Show all does not restore model marks');
      assert.equal(new URL(page.url()).searchParams.has('radar-language'), false, 'Show all leaves stale selection restriction');
      // Restore through history events too: controls and series must agree.
      await page.evaluate(saved => {
        history.pushState(null, '', saved);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, savedRadarURL);
      assert.deepEqual(await selection(), oneOff, 'History event does not restore model selection');
      const synced = await page.locator('[data-radar-group="language"]').evaluateAll((nodes, model) => nodes.map(node => {
        const control = [...node.querySelectorAll('[data-radar-toggle]')].find(item => item.value === model);
        const series = [...node.querySelectorAll('[data-radar-series]')].find(item => item.dataset.radarSeries === model);
        return control ? { checked: control.checked, hidden: series?.hasAttribute('hidden') } : null;
      }).filter(Boolean), firstModel);
      assert(synced.length > 1 && synced.every(row => !row.checked && row.hidden), 'Same dimension selection differs between cohort/scope views');
      await radar.locator('[data-radar-all]').click();
      evidence.interactions = { models: oneOff.length, keyboardToggle: true, readout: true,
        restoredReloadBackAndHistory: true, explicitEmptySelection: true, synchronizedViews: synced.length };
      for (const width of [390, 768, 1440]) {
        await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
        await activeScope().locator('.capability-dimension-group,.dimension-radars').evaluateAll(nodes => {
          for (const node of nodes) node.open = true;
        });
        const geometry = await activeScope().evaluate(node => {
          const svgBounds = [...node.querySelectorAll('.dimension-radar .radar-plot > svg')].map(svg => {
            const box = svg.viewBox.baseVal;
            const labels = [...svg.querySelectorAll('text')].map(label => {
              const bounds = label.getBBox();
              return { text: label.textContent, x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
                clipped: bounds.x < box.x - 1 || bounds.y < box.y - 1 || bounds.x + bounds.width > box.x + box.width + 1 || bounds.y + bounds.height > box.y + box.height + 1 };
            });
            const series = [...svg.querySelectorAll('[data-radar-series]')].map(item => ({
              model: item.dataset.radarSeries, observedAxes: Number(item.dataset.observedAxes),
              points: item.querySelectorAll('.dimension-point').length,
              polygons: item.querySelectorAll('.dimension-profile').length,
            }));
            return { series, axes: svg.querySelectorAll('.dimension-spoke').length,
              labels, labelOverflows: labels.filter(label => label.clipped).length };
          });
          return { width: innerWidth, documentWidth: document.documentElement.scrollWidth,
            groups: node.querySelectorAll('.capability-dimension-group[open]').length,
            duplicateRadars: [...node.querySelectorAll('.capability-dimension-group')].filter(group => group.querySelectorAll('.dimension-radar .radar-plot > svg').length > 1).length,
            svgBounds };
        });
        assert(geometry.documentWidth <= width + 1, `Open dimension profiles overflow to ${geometry.documentWidth}px at ${width}px`);
        assert(geometry.groups >= 3 && geometry.svgBounds.length > 0, 'Dimension disclosures lack profile diagrams');
        assert.equal(geometry.duplicateRadars, 0, 'A dimension group has more than one radar');
        for (const radar of geometry.svgBounds) {
          assert.equal(radar.labelOverflows, 0, 'Radar label exceeds its SVG viewBox: ' + radar.labels.filter(label => label.clipped).map(label => label.text).join(', '));
          for (const series of radar.series) {
            assert.equal(series.points, series.observedAxes, 'Radar uses unmeasured axes as observed points');
            assert.equal(series.polygons, series.observedAxes === radar.axes ? 1 : 0, 'Incomplete radar joins unknown values into a polygon');
          }
        }
        evidence.layouts.push(geometry);
      }
      await language.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, 'dimension-profile.png') });
      await selector.selectOption('all-models');
      assert.equal(new URL(page.url()).searchParams.get('cohort'), 'all-models');
      const sharedText = await activeCohort().locator('[data-chart-scope="matched"]').innerText();
      const sharedCases = Number(sharedText.match(/\((\d+)\)/)?.[1]);
      assert(Number.isFinite(sharedCases), 'Shared cohort has no declared case count');
      if (sharedCases === 0) {
        assert.match(await activeScope().innerText(), /No common graded cases/);
        assert.equal(await activeScope().locator('.capability-dimensions').count(), 0, 'Empty common cohort shows invented profiles');
      } else {
        assert(await activeScope().locator('.capability-dimensions').isVisible(), 'Existing shared evidence is hidden as unmeasured');
      }
      await activeCohort().locator('[data-chart-scope="all"]').click();
      assert.equal(new URL(page.url()).searchParams.get('scope'), 'all');
      assert(await activeScope().locator('.capability-dimensions').isVisible(), 'Own-subset scope does not restore observed profiles');
      await activeScope().locator('.capability-dimension-group,.dimension-radars').evaluateAll(nodes => {
        for (const node of nodes) node.open = true;
      });
      const markers = await activeScope().evaluate(node => ({
        measured: node.querySelectorAll('.dimension-measured').length,
        unmeasured: node.querySelectorAll('.dimension-unmeasured').length,
        measuredZero: [...node.querySelectorAll('.dimension-measured')].filter(cell => parseFloat(cell.querySelector('strong').textContent) === 0).length,
        badUnknown: [...node.querySelectorAll('.dimension-unmeasured')].filter(cell => !cell.textContent.includes('Unmeasured') || cell.classList.contains('dimension-measured') || cell.querySelector('strong')).length,
        badMeasured: [...node.querySelectorAll('.dimension-measured')].filter(cell => !Number.isFinite(parseFloat(cell.querySelector('strong')?.textContent)) || !/n=[1-9]\d*/.test(cell.textContent)).length,
        incomplete: [...node.querySelectorAll('[data-radar-series]')].filter(series => Number(series.dataset.observedAxes) < series.ownerSVGElement.querySelectorAll('.dimension-spoke').length).length,
        invalidPolygons: [...node.querySelectorAll('[data-radar-series]')].filter(series => Number(series.dataset.observedAxes) < series.ownerSVGElement.querySelectorAll('.dimension-spoke').length && series.querySelector('.dimension-profile')).length,
      }));
      assert(markers.measured > 0 && markers.unmeasured > 0, 'Own-subset view does not distinguish measured and absent evidence');
      assert.equal(markers.badUnknown, 0, 'Missing data displays a measured zero');
      assert.equal(markers.badMeasured, 0, 'Measured scores lack a nonempty evidence denominator');
      assert.equal(markers.invalidPolygons, 0, 'Incomplete profiles connect unknown values');
      await page.reload();
      assert.equal(await selector.inputValue(), 'all-models');
      assert.equal(await activeScope().getAttribute('data-chart-panel'), 'all', 'Shared URL loses own-subset scope');
      assert.equal(pageErrors.length, initialErrors, 'Dimension interactions raised a browser error');
      assert.equal(blockedRequests, initialRequests, 'Dimension profiles attempted external requests');
      return { ...evidence, allModelsSharedCases: sharedCases, emptySharedCohortPresent: sharedCases === 0,
        markers, restoredOwnSubset: true };
    });
    await check('malformed fragment does not disable later evidence links', async () => {
      const initialErrors = pageErrors.length;
      for (const fragment of ['', '%', '%E6%B5%8B%E8%AF%95', 'no-such-evidence']) {
        await page.goto(url('index.html') + '#' + fragment);
      }
      await page.evaluate(() => {
        const detail = document.createElement('details');
        detail.innerHTML = '<summary>Anchor control</summary><span id="review-anchor-control">Evidence</span>';
        document.querySelector('main').append(detail);
        location.hash = 'review-anchor-control';
      });
      await page.waitForFunction(() => document.getElementById('review-anchor-control').closest('details').open);
      assert.equal(pageErrors.length, initialErrors, 'Fragment parsing raised a browser error');
      return { fragmentFixtures: 4, nestedDisclosureOpened: true };
    });
    for (const width of [320, 360, 390, 768, 1440]) {
      await check('layout at ' + width + ' pixels', async () => {
        await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
        const measurements = [];
        for (const name of ['index.html', 'capabilities.html', 'methodology.html', 'costs.html', 'cases/index.html']) {
          await page.goto(url(name));
          const geometry = await page.evaluate(() => ({
            width: innerWidth, documentWidth: document.documentElement.scrollWidth,
            documentHeight: document.documentElement.scrollHeight,
            headerHeight: document.querySelector('header').getBoundingClientRect().height,
            comparisonY: document.getElementById('comparison')?.getBoundingClientRect().top,
            observationsY: document.getElementById('observations')?.getBoundingClientRect().top,
            historyRows: document.querySelectorAll('#typed-history tbody tr').length,
            cardSizes: [...document.querySelectorAll('.coverage-card')].map(card => ({
              width: card.getBoundingClientRect().width,
              fontSizes: [...card.querySelectorAll('dt')].map(node => parseFloat(getComputedStyle(node).fontSize))
            })),
          }));
          assert(geometry.documentWidth <= width + 1, `${name} is ${geometry.documentWidth}px wide at ${width}px`);
          if (width < 768) assert(geometry.headerHeight < 440, `${name} mobile header consumes more than half a screen`);
          if (name === 'capabilities.html') {
            assert(geometry.cardSizes.length > 0, 'Capability inventory still lacks responsive HTML cards');
            assert(geometry.cardSizes.every(card => card.fontSizes.length && card.fontSizes.every(size => size >= 14)), 'Capability labels shrink below 14px');
          }
          measurements.push({ page: name, ...geometry });
        }
        await page.goto(url('index.html'));
        await page.screenshot({ path: path.join(output, 'overview-' + width + '.png') });
        return measurements;
      });
    }
    await check('keyboard exact token values and navigation semantics', async () => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(url('costs.html'));
      const details = page.locator('details.chart-data').filter({ hasText: /Fresh input|Cache writes/i }).first();
      if (await details.count()) {
        const summary = details.locator('summary');
        await summary.focus();
        await page.keyboard.press('Enter');
        assert(await details.evaluate(node => node.open), 'Numerical disclosure is not keyboard operable');
        assert(await details.locator('table').isVisible());
      } else {
        const table = page.locator('#native-usage');
        assert(await table.isVisible(), 'No visible table of exact token categories');
        assert.match(await table.locator('thead').innerText(), /Fresh input/);
        assert.match(await table.locator('thead').innerText(), /Cache writes/);
        const region = table.locator('xpath=..');
        await region.focus();
        assert(await region.evaluate(node => node === document.activeElement), 'Exact token region cannot receive keyboard focus');
      }
      assert(await page.locator('a[href="#main-content"],a.skip-link').count(), 'Skip navigation is absent');
      assert(await page.locator('nav [aria-current="page"]').count(), 'Current navigation item is unmarked');
      return { numericalTables: await page.locator('details.chart-data table,#native-usage').count() };
    });
    await check('model table links agree with destination sections', async () => {
      await page.goto(url('cases/index.html'));
      const links = await page.locator('#all-cases tbody a[href*=".html"]').evaluateAll(nodes => nodes.map(node => node.href));
      const byFile = new Map();
      for (const href of links) {
        const target = new URL(href);
        assert(target.hash, 'Case result link has no model identity');
        const id = decodeURIComponent(target.hash.slice(1));
        target.hash = '';
        if (!byFile.has(target.href)) byFile.set(target.href, new Set());
        byFile.get(target.href).add(id);
      }
      for (const [file, ids] of byFile) {
        await page.goto(file);
        const found = await page.locator('[id]').evaluateAll(nodes => nodes.map(node => node.id));
        for (const id of ids) assert(found.includes(id), `Missing model ${id} at ${file}`);
      }
      return { links: links.length, files: byFile.size };
    });
    await check('heatmap text bounds and persistent identities', async () => {
      const states = [];
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.goto(url('cases/index.html'));
        const region = page.locator('.heatmap-region').first();
        const state = await region.evaluate(node => {
          node.scrollTop = node.scrollHeight;
          node.scrollLeft = node.scrollWidth;
          const bounds = node.getBoundingClientRect();
          const header = node.querySelector('thead th').getBoundingClientRect();
          const identity = [...node.querySelectorAll('tbody th')].find(cell => {
            const rect = cell.getBoundingClientRect();
            return rect.top >= header.bottom && rect.bottom <= bounds.bottom;
          });
          return { width: innerWidth, scrollTop: node.scrollTop, scrollLeft: node.scrollLeft,
            headerTop: header.top - bounds.top,
            identityLeft: identity ? identity.getBoundingClientRect().left - bounds.left : null,
            overflowingCells: [...node.querySelectorAll('.heat-cell')].filter(cell => cell.scrollWidth > cell.clientWidth + 1).length };
        });
        assert.equal(state.overflowingCells, 0, 'Text crosses heatmap cell boundaries');
        assert(state.headerTop >= 0 && state.headerTop <= 3, 'Scrolled heatmap loses its model header');
        assert(state.identityLeft !== null && state.identityLeft >= 0 && state.identityLeft <= 3,
          'Scrolled heatmap loses its case identity');
        states.push(state);
      }
      return states;
    });
    if (fs.existsSync(path.join(localSite, 'index.html'))) {
      await check('public and local values agree for the same saved scope', async () => {
        const compared = [];
        const tableValues = tables => tables.map(table => ({
          caption: (table.querySelector('caption')?.textContent || '').replace(/\s+/g, ' ').trim(),
          headers: [...table.querySelectorAll('thead tr')].map(row => [...row.children].map(cell => cell.textContent.replace(/\s+/g, ' ').trim())),
          rows: [...table.querySelectorAll('tbody tr')].map(row => [...row.children].map(cell => cell.textContent.replace(/\s+/g, ' ').trim()))
            .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
        }));
        for (const [name, selector] of [
          ['comparison.html', '[data-cohort-panel="all-models"] [data-chart-panel="matched"] table'],
          ['costs.html', '#expenditure table'],
        ]) {
          await page.goto(url(name));
          const published = await page.locator(selector).evaluateAll(tableValues);
          const publicOrder = await page.locator(selector).first().locator('tbody tr').evaluateAll(rows => rows.map(row => row.firstElementChild.textContent.replace(/\s+/g, ' ').trim()));
          assert(published.length, 'Expected public comparison table is absent');
          await page.goto(pathToFileURL(path.join(localSite, name)).href);
          const privateView = await page.locator(selector).evaluateAll(tableValues);
          const localOrder = await page.locator(selector).first().locator('tbody tr').evaluateAll(rows => rows.map(row => row.firstElementChild.textContent.replace(/\s+/g, ' ').trim()));
          assert(JSON.stringify(privateView) === JSON.stringify(published),
            `${name} named rows, values, denominators or cohort membership differ across public and local renderers`);
          compared.push({ page: name, tables: published.length, displayOrderEqual: JSON.stringify(publicOrder) === JSON.stringify(localOrder),
            publicOrder, localOrder });
        }
        return compared;
      });
    }
    await check('print styles and selected scope PDFs', async () => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(url('index.html') + '?scope=matched');
      await page.emulateMedia({ media: 'print' });
      const state = await page.evaluate(() => ({
        navigationVisible: document.querySelector('header nav').getClientRects().length > 0,
        hiddenPanels: [...document.querySelectorAll('[data-chart-panel][hidden]')].every(node => getComputedStyle(node).display === 'none'),
        context: document.querySelector('.print-context')?.innerText || '',
      }));
      assert.equal(state.navigationVisible, false, 'Print keeps the interactive navigation');
      assert(state.hiddenPanels, 'Print unexpectedly includes an unselected population');
      assert(state.context.length > 0, 'Print lacks scope identity');
      if (args.includes('--pdf')) {
        // The production export defaults to landscape. Explicitly select the
        // alternate page layout instead of misnaming a CSS-forced landscape PDF.
        const portraitStyle = await page.addStyleTag({ content: '@page{size:A4 portrait}' });
        const portrait = await page.pdf({ path: path.join(output, 'overview-a4-portrait.pdf'),
          format: 'A4', printBackground: true, preferCSSPageSize: true });
        await portraitStyle.evaluate(node => node.remove());
        const landscape = await page.pdf({ path: path.join(output, 'overview-a4-landscape.pdf'),
          format: 'A4', landscape: true, printBackground: true, preferCSSPageSize: true });
        const boxes = buffer => [...buffer.toString('latin1').matchAll(/\/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]/g)]
          .map(match => [Number(match[1]), Number(match[2])]);
        const portraitBoxes = boxes(portrait), landscapeBoxes = boxes(landscape);
        assert(portraitBoxes.length && portraitBoxes.every(([width, height]) => height > width), 'Portrait export is not actually portrait');
        assert(landscapeBoxes.length && landscapeBoxes.every(([width, height]) => width > height), 'Landscape export is not actually landscape');
        state.pdfPageCounts = { portrait: portraitBoxes.length, landscape: landscapeBoxes.length };
      }
      await page.emulateMedia({ media: 'screen' });
      return { ...state, pdfGenerated: args.includes('--pdf') };
    });
    await check('no browser script errors or external resource dependencies', async () => {
      assert.deepEqual(pageErrors, []);
      assert.equal(blockedRequests, 0, 'Report attempted an external request');
      return { pageErrors: 0, externalRequests: 0 };
    });
  } finally {
    await browser.close();
    const fileHash = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
    const report = { site: path.relative(root, site), measuredAt: new Date().toISOString(), browser: browserVersion,
      indexSha256: fileHash(path.join(site, 'index.html')),
      stylesheetSha256: fileHash(path.join(site, 'assets/report.css')),
      interactionsSha256: fileHash(path.join(site, 'assets/report.js')),
      checkScriptSha256: fileHash(__filename), selectedCheckSubstring: only || null,
      dimensionsSha256: fs.existsSync(path.join(site, 'dimensions.html')) ? fileHash(path.join(site, 'dimensions.html')) : null,
      passed: results.length > 0 && results.every(item => item.passed),
      modelApiCalls: 0, blockedRequests, checks: results, pageErrors };
    fs.writeFileSync(path.join(output, 'acceptance.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(`${results.filter(item => item.passed).length}/${results.length} browser checks passed`);
    if (!report.passed) process.exitCode = 1;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
