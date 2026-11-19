document.addEventListener('DOMContentLoaded', () => {
  // Live Client-side search for accordion & card items
  const searchInput = document.getElementById('portalLiveSearch');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const term = e.target.value.toLowerCase().trim();
      const searchableItems = document.querySelectorAll('.searchable-item');

      searchableItems.forEach((item) => {
        const text = item.textContent.toLowerCase();
        if (text.includes(term)) {
          item.style.display = '';
        } else {
          item.style.display = 'none';
        }
      });
    });
  }
});

/* ======================================================================
   Enrollment / prospectus helpers (vanilla JS, no extra libraries)
   ====================================================================== */

/* 1. Searchable <select>: <select data-searchable data-search-min="6">
      A search box appears above the dropdown once it has >= N options. */
(function () {
  function enhance(select) {
    if (select.dataset.searchReady) return;
    var min = parseInt(select.dataset.searchMin || '6', 10);
    var all = Array.prototype.slice.call(select.options).map(function (o) {
      return { value: o.value, text: o.text, disabled: o.disabled };
    });
    select.dataset.searchReady = '1';
    if (all.length < min) return;

    var box = document.createElement('input');
    box.type = 'search';
    box.className = 'form-control form-control-sm searchable-select-input';
    box.placeholder = 'Type to search programs...';
    box.setAttribute('aria-label', 'Search options');
    select.parentNode.insertBefore(box, select);

    box.addEventListener('input', function () {
      var term = box.value.toLowerCase().trim();
      var keep = select.value;
      select.innerHTML = '';
      all.forEach(function (o) {
        // keep the placeholder (empty value) and the current selection visible
        if (o.value === '' || o.value === keep || o.text.toLowerCase().indexOf(term) !== -1) {
          var opt = new Option(o.text, o.value, false, o.value === keep);
          opt.disabled = o.disabled;
          select.appendChild(opt);
        }
      });
    });
    select._searchAll = all;
    select._searchBox = box;
  }

  /* Programmatically select a value (used by the shared "Change program" modal). */
  window.setSearchableValue = function (select, value) {
    if (select._searchBox) { select._searchBox.value = ''; }
    if (select._searchAll) {
      select.innerHTML = '';
      select._searchAll.forEach(function (o) {
        select.appendChild(new Option(o.text, o.value, false, o.value === String(value)));
      });
    }
    select.value = String(value);
  };

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('select[data-searchable]').forEach(enhance);
  });
})();

/* 2. Show the program dropdown only for STUDENT accounts:
      <select data-role-toggle> controls the closest form's [data-student-only] block. */
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('select[data-role-toggle]').forEach(function (roleSelect) {
    var form = roleSelect.closest('form');
    if (!form) return;
    var blocks = form.querySelectorAll('[data-student-only]');
    function sync() {
      var isStudent = roleSelect.value === 'STUDENT';
      blocks.forEach(function (block) {
        block.style.display = isStudent ? '' : 'none';
        // disabled fields are not submitted, so a hidden dropdown can never alter an enrollment
        block.querySelectorAll('select, input').forEach(function (el) { el.disabled = !isStudent; });
      });
    }
    roleSelect.addEventListener('change', sync);
    sync();
  });
});

/* 3. Live table filter: <input data-table-search="#tableId"> */
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('input[data-table-search]').forEach(function (input) {
    var table = document.querySelector(input.dataset.tableSearch);
    if (!table) return;
    input.addEventListener('input', function () {
      var term = input.value.toLowerCase().trim();
      table.querySelectorAll('tbody tr').forEach(function (row) {
        row.style.display = row.textContent.toLowerCase().indexOf(term) !== -1 ? '' : 'none';
      });
    });
  });
});

/* 4. CSRF for any future AJAX call: window.csrfFetch(url, options) */
window.csrfFetch = function (url, options) {
  options = options || {};
  var tokenInput = document.querySelector('input[name="csrf_token"]');
  options.headers = Object.assign({ 'X-CSRFToken': tokenInput ? tokenInput.value : '' }, options.headers || {});
  options.credentials = options.credentials || 'same-origin';
  return fetch(url, options);
};

/* 5. Confirmation dialogs: <form data-confirm="Are you sure?">
      Uses a data attribute (not inline onsubmit JS) so names containing quotes can never break the
      handler and silently skip the confirmation. */
document.addEventListener('submit', function (ev) {
  var form = ev.target;
  if (form && form.matches && form.matches('form[data-confirm]')) {
    if (!window.confirm(form.getAttribute('data-confirm'))) { ev.preventDefault(); }
  }
});
