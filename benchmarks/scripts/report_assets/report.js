/* Portable report interactions. URL state wins when supplied; otherwise preserve
   browser-restored controls on pageshow (including the back/forward cache). */
(() => {
  'use strict';
  const filters = [];
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const params = () => new URL(location.href).searchParams;
  function saveState(key, value) {
    const url = new URL(location.href);
    value ? url.searchParams.set(key, value) : url.searchParams.delete(key);
    if (url.href !== location.href) history.replaceState(null, '', url.href);
  }
  function applyFilter(control, persist = false) {
    const query = control.input.value.trim().toLocaleLowerCase();
    const matching = [];
    control.rows.forEach(row => {
      const textMatches = row.textContent.toLocaleLowerCase().includes(query);
      const fieldsMatch = control.fields.every(({select, value}) => !select.value || value(row) === select.value);
      row.hidden = !(textMatches && fieldsMatch);
      if (!row.hidden) matching.push(row);
    });
    if (persist) control.page = 0;
    const pages = Math.max(1, Math.ceil(matching.length / control.pageSize));
    control.page = Math.min(control.page, pages - 1);
    matching.forEach((row, index) => {row.hidden = index < control.page * control.pageSize || index >= (control.page + 1) * control.pageSize;});
    const visible = matching.filter(row => !row.hidden).length;
    control.status.textContent = `${matching.length} of ${control.rows.length} rows match; ${visible} shown. Filters affect this table only; charts retain their stated population.`;
    control.empty.hidden = matching.length !== 0;
    if (control.pagination) {
      control.pagination.hidden = pages <= 1;
      control.pageLabel.textContent = `Page ${control.page + 1} of ${pages}`;
      control.previous.disabled = control.page === 0; control.next.disabled = control.page + 1 >= pages;
    }
    if (persist) {
      saveState(control.key, control.input.value);
      control.fields.forEach(field => saveState(`${control.key}-${field.name}`, field.select.value));
    }
  }
  all('table[data-table-search]').forEach((table, index) => {
    if (!table.id) table.id = `searchable-table-${index + 1}`;
    if (all('[data-table-filter]').some(input => input.dataset.tableFilter === table.id)) return;
    const label = document.createElement('label'); label.textContent = 'Search this table ';
    const input = document.createElement('input'); input.dataset.tableFilter = table.id;
    input.setAttribute('aria-label', 'Search ' + (table.caption?.textContent || 'results'));
    label.append(input); (table.closest('.scroll') || table).before(label);
  });
  all('[data-table-filter]').forEach(input => {
    const table = document.getElementById(input.dataset.tableFilter);
    if (!table) return;
    const key = `filter-${table.id}`;
    const rows = all('tbody tr', table);
    const status = document.createElement('p');
    status.className = 'filter-status'; status.id = `${table.id}-filter-status`;
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    input.setAttribute('aria-controls', table.id); input.setAttribute('aria-describedby', status.id);
    const controls = document.createElement('div'); controls.className = 'filter-controls';
    const fields = [];
    const headers = all('thead th', table);
    ['model', 'status', 'language'].forEach(name => {
      const index = headers.findIndex(h => h.textContent.trim().toLocaleLowerCase() === name);
      const attribute = `filter${name[0].toUpperCase()}${name.slice(1)}`;
      if (index < 0 && !rows.some(row => row.dataset[attribute])) return;
      const valuesByRow = new Map(rows.map(row => {
        if (row.dataset[attribute]) return [row, row.dataset[attribute]];
        const cell = index < 0 ? null : row.children[index]?.cloneNode(true);
        // Model annotations describe the condition/evidence, not model identity.
        if (name === 'model' && cell) all('small', cell).forEach(note => note.remove());
        return [row, cell?.textContent.trim() || ''];
      }));
      const value = row => valuesByRow.get(row) || '';
      const values = [...new Set(rows.map(value).filter(Boolean))].sort();
      if (values.length < 2) return;
      const label = document.createElement('label'); label.textContent = `${name[0].toUpperCase()}${name.slice(1)} `;
      const select = document.createElement('select');
      select.add(new Option('All', ''));
      values.forEach(item => select.add(new Option(item, item)));
      label.append(select); controls.append(label);
      fields.push({name, select, value});
    });
    const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = 'Clear filters'; controls.append(clear);
    const empty = document.createElement('p'); empty.className = 'filter-empty'; empty.hidden = true;
    empty.textContent = 'No matching rows. Change the search or clear filters to restore the list.';
    const region = table.closest('.scroll') || table;
    region.before(controls, status); region.after(empty);
    const control = {input, table, rows, key, status, fields, empty, page: 0, pageSize: Number(table.dataset.pageSize) || Math.max(rows.length, 1)};
    if (rows.length > control.pageSize) {
      const pagination = document.createElement('div'); pagination.className = 'pagination';
      const previous = document.createElement('button'); previous.type = 'button'; previous.textContent = 'Previous page';
      const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Next page';
      const pageLabel = document.createElement('span'); pagination.append(previous, pageLabel, next); region.after(pagination);
      Object.assign(control, {pagination, previous, next, pageLabel});
      previous.addEventListener('click', () => {control.page--; applyFilter(control);});
      next.addEventListener('click', () => {control.page++; applyFilter(control);});
    }
    filters.push(control);
    input.addEventListener('input', () => applyFilter(control, true));
    fields.forEach(({select}) => select.addEventListener('change', () => applyFilter(control, true)));
    clear.addEventListener('click', () => {
      input.value = ''; fields.forEach(({select}) => {select.value = '';});
      applyFilter(control, true); input.focus();
    });
  });
  const radarFigures = all('[data-radar-group]');
  const radarSelections = new Map();
  const radarModels = key => [...new Set(radarFigures.filter(figure => figure.dataset.radarGroup === key)
    .flatMap(figure => all('[data-radar-toggle]', figure).map(input => input.value)))];
  function setRadarSelection(key, selection, persist = false) {
    const selected = selection === null ? null : new Set(selection);
    radarSelections.set(key, selected);
    radarFigures.filter(figure => figure.dataset.radarGroup === key).forEach(figure => {
      const controls = all('[data-radar-toggle]', figure);
      controls.forEach(input => {input.checked = selected === null || selected.has(input.value);});
      all('[data-radar-series]', figure).forEach(series => {
        const visible = selected === null || selected.has(series.dataset.radarSeries);
        series.toggleAttribute('hidden', !visible);
        series.setAttribute('aria-hidden', String(!visible));
        series.classList.remove('is-muted', 'is-highlighted');
      });
      const shown = controls.filter(input => input.checked).length;
      figure.querySelector('[data-radar-status]').textContent = `${shown}/${controls.length} models shown. Selection changes chart visibility only; the scored cohort and tables stay unchanged.`;
      figure.querySelector('[data-radar-empty]').hidden = shown !== 0;
      figure.querySelector('[data-radar-readout]').textContent = 'Hover or focus a point to inspect its score and sample count.';
    });
    if (persist) {
      const url = new URL(location.href);
      const parameter = `radar-${key}`;
      if (selected === null) url.searchParams.delete(parameter);
      else url.searchParams.set(parameter, [...selected].sort().join(',')); // Empty means none, not all.
      if (url.href !== location.href) history.replaceState(null, '', url.href);
    }
  }
  function highlightRadar(figure, model) {
    all('[data-radar-series]', figure).forEach(series => {
      series.classList.toggle('is-muted', model !== null && series.dataset.radarSeries !== model);
      series.classList.toggle('is-highlighted', series.dataset.radarSeries === model);
    });
  }
  radarFigures.forEach(figure => {
    const key = figure.dataset.radarGroup;
    all('[data-radar-toggle]', figure).forEach(input => {
      input.addEventListener('change', () => {
        const selected = new Set(radarSelections.get(key) ?? radarModels(key));
        input.checked ? selected.add(input.value) : selected.delete(input.value);
        setRadarSelection(key, selected, true);
      });
      const label = input.closest('label');
      label.addEventListener('pointerenter', () => highlightRadar(figure, input.value));
      label.addEventListener('pointerleave', () => highlightRadar(figure, null));
      input.addEventListener('focus', () => highlightRadar(figure, input.value));
      input.addEventListener('blur', () => highlightRadar(figure, null));
    });
    figure.querySelector('[data-radar-all]').addEventListener('click', () => setRadarSelection(key, null, true));
    figure.querySelector('[data-radar-none]').addEventListener('click', () => setRadarSelection(key, [], true));
    all('[data-radar-tip]', figure).forEach(point => {
      const show = () => {
        figure.querySelector('[data-radar-readout]').textContent = window.REA_I18N?.rawAttribute(point, 'data-radar-tip') ?? point.dataset.radarTip;
        highlightRadar(figure, point.closest('[data-radar-series]').dataset.radarSeries);
      };
      const clear = () => highlightRadar(figure, null);
      point.addEventListener('pointerenter', show);
      point.addEventListener('focus', show);
      point.addEventListener('pointerleave', clear);
      point.addEventListener('blur', clear);
    });
  });
  function updatePrintScope() {
    const cohort = all('[data-cohort-panel]').find(panel => !panel.hidden);
    const scope = all('[data-chart-scope][aria-pressed="true"]', cohort || document).find(button => !button.closest('[hidden]'));
    const selector = document.querySelector('[data-cohort-selection]');
    const cohortLabel = selector?.selectedOptions[0] && (window.REA_I18N?.rawText(selector.selectedOptions[0]) ?? selector.selectedOptions[0].textContent).trim();
    const text = [cohortLabel, scope && (window.REA_I18N?.rawText(scope) ?? scope.textContent).trim()].filter(Boolean).join(' · ');
    if (text) all('[data-print-scope]').forEach(label => {label.textContent = text;});
  }
  function applyScope(scope, persist = false) {
    const buttons = all('[data-chart-scope]');
    if (!buttons.some(button => button.dataset.chartScope === scope)) return;
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.chartScope === scope)));
    all('[data-chart-panel]').forEach(panel => {panel.hidden = panel.dataset.chartPanel !== scope;});
    updatePrintScope();
    if (persist) saveState('scope', scope);
  }
  all('[data-chart-scope]').forEach(button => button.addEventListener('click', () => applyScope(button.dataset.chartScope, true)));
  function applyCohort(key, persist = false) {
    if (!all('[data-cohort-panel]').some(panel => panel.dataset.cohortPanel === key)) return;
    all('[data-cohort-panel]').forEach(panel => {panel.hidden = panel.dataset.cohortPanel !== key;});
    all('[data-cohort-selection]').forEach(select => {select.value = key;});
    updatePrintScope();
    if (persist) saveState('cohort', key);
  }
  all('[data-cohort-selection]').forEach(select => select.addEventListener('change', () => applyCohort(select.value, true)));
  const defaultScope = document.querySelector('[data-chart-scope][aria-pressed="true"]')?.dataset.chartScope;
  const defaultCohort = document.querySelector('[data-cohort-selection]')?.value;
  function restoreState(event) {
    const canonicalURL = event?.type === 'popstate';
    const query = params();
    filters.forEach(control => {
      if (query.has(control.key) || canonicalURL) control.input.value = query.get(control.key) || "";
      control.fields.forEach(field => {
        if (query.has(`${control.key}-${field.name}`) || canonicalURL) field.select.value = query.get(`${control.key}-${field.name}`) || "";
      });
      applyFilter(control);
    });
    applyScope(query.get('scope') || (canonicalURL ? defaultScope : document.querySelector('[data-chart-scope][aria-pressed="true"]')?.dataset.chartScope));
    applyCohort(query.get('cohort') || (canonicalURL ? defaultCohort : document.querySelector('[data-cohort-selection]')?.value));
    [...new Set(radarFigures.map(figure => figure.dataset.radarGroup))].forEach(key => {
      const known = radarModels(key);
      setRadarSelection(key, query.has(`radar-${key}`) ? (query.get(`radar-${key}`) || '').split(',').filter(model => known.includes(model)) : null);
    });
  }
  function revealLinkedEvidence() {
    let id;
    try {id = decodeURIComponent(location.hash.slice(1));} catch (_) {return;}
    if (!id) return;
    const target = document.getElementById(id);
    if (!target) return;
    let node = target;
    while (node) {if (node.tagName === 'DETAILS') node.open = true; node = node.parentElement;}
  }
  window.addEventListener('rea:languagechange', () => {filters.forEach(control => applyFilter(control));updatePrintScope();});
  window.addEventListener('hashchange', revealLinkedEvidence);
  window.addEventListener('pageshow', restoreState);
  window.addEventListener('popstate', restoreState);
  restoreState(); revealLinkedEvidence();
  const navigation = document.querySelector('.report-navigation');
  if (navigation && window.matchMedia('(max-width:600px)').matches) navigation.open = false;
  all('nav a').forEach(link => {
    const target = new URL(link.href, location.href);
    if (target.pathname === location.pathname) link.setAttribute('aria-current', 'page');
  });
  all('.scroll').forEach((region, index) => {
    region.tabIndex = 0; region.setAttribute('role', 'region');
    const caption = region.querySelector('caption');
    region.setAttribute('aria-label', caption?.textContent.trim() || `Scrollable results table ${index + 1}`);
  });
  all('.segment').forEach(segment => {
    // Exact numbers are available in adjacent HTML tables; bars stay decorative
    // for keyboard navigation instead of adding dozens of redundant focus stops.
    segment.addEventListener('pointerleave', () => segment.classList.remove('tip-dismissed'));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') all('.segment').forEach(segment => segment.classList.add('tip-dismissed'));
  });
  let printDetails = [];
  window.addEventListener('beforeprint', () => {
    const toOpen = new Set();
    all('details.comparison-charts, details.chart-data').filter(detail => !detail.closest('[hidden]')).forEach(detail => {
      let ancestor = detail;
      while (ancestor) {
        if (ancestor.tagName === 'DETAILS' && !ancestor.open) toOpen.add(ancestor);
        ancestor = ancestor.parentElement;
      }
    });
    printDetails = [...toOpen];
    printDetails.forEach(detail => {detail.open = true;});
    updatePrintScope();
  });
  window.addEventListener('afterprint', () => {
    printDetails.forEach(detail => {detail.open = false;}); printDetails = [];
  });
  all('[data-print-report]').forEach(button => button.addEventListener('click', () => window.print()));
})();
