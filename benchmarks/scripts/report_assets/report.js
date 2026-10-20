document.querySelectorAll('[data-table-filter]').forEach(input => {
  input.addEventListener('input', () => {
    const query = input.value.trim().toLowerCase();
    const table = document.getElementById(input.dataset.tableFilter);
    table.querySelectorAll('tbody tr').forEach(row => {
      row.hidden = !row.textContent.toLowerCase().includes(query);
    });
  });
});

document.querySelectorAll('[data-chart-scope]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-chart-scope]').forEach(item => {
      item.setAttribute('aria-pressed', String(item === button));
    });
    document.querySelectorAll('[data-chart-panel]').forEach(panel => {
      panel.hidden = panel.dataset.chartPanel !== button.dataset.chartScope;
    });
  });
});

function revealLinkedEvidence() {
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target?.tagName === 'DETAILS') target.open = true;
}
revealLinkedEvidence();
window.addEventListener('hashchange', revealLinkedEvidence);
