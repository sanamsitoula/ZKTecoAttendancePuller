/**
 * Bikram Sambat (Nepali) Date Picker
 * Pure vanilla JS — no dependencies.
 * Uses backend /api/bs-month-info, /api/bs-to-ad, /api/ad-to-bs for accurate conversion.
 *
 * Usage:
 *   <input class="bsdp" name="from_bs" data-ad-target="from_date" placeholder="YYYY-MM-DD BS">
 *   <input type="date" id="from_date" data-bs-target="from_bs">
 */
(function () {
  'use strict';

  var MONTHS = ['','Baisakh','Jestha','Ashadh','Shrawan','Bhadra','Ashwin',
                'Kartik','Mangsir','Poush','Magh','Falgun','Chaitra'];
  var DAY_HDR = ['Su','Mo','Tu','We','Th','Fr','Sa'];

  var _panel = null;
  var _curInput = null;
  var _curYear = 2082;
  var _curMonth = 1;
  var _loadingToken = 0;

  /* ── panel creation ─────────────────────────────────────────── */
  function _mkPanel() {
    var p = document.createElement('div');
    p.id = 'bsdp-panel';
    p.innerHTML =
      '<div class="bsdp-nav">' +
        '<button class="bsdp-btn" id="bsdp-prev" type="button">&#8249;</button>' +
        '<span id="bsdp-title"></span>' +
        '<button class="bsdp-btn" id="bsdp-next" type="button">&#8250;</button>' +
      '</div>' +
      '<div id="bsdp-grid"></div>' +
      '<div id="bsdp-load">Loading…</div>';
    document.body.appendChild(p);

    p.querySelector('#bsdp-prev').addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation(); _nav(-1);
    });
    p.querySelector('#bsdp-next').addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation(); _nav(1);
    });
    document.addEventListener('mousedown', function (e) {
      if (_panel && _panel.style.display !== 'none' &&
          !_panel.contains(e.target) && e.target !== _curInput) {
        _hide();
      }
    });
    return p;
  }

  function _show(input) {
    if (!_panel) _panel = _mkPanel();
    _curInput = input;

    /* determine which month to open */
    var val = input.value;
    var y = _curYear, m = _curMonth;
    if (/^\d{4}-\d{2}-\d{2}$/.test(val)) {
      var p = val.split('-'); y = +p[0]; m = +p[1];
    } else {
      /* try to use today's BS date from data attribute or first BSDP input */
      var def = input.dataset.bsDefault;
      if (def && /^\d{4}-\d{2}/.test(def)) {
        var dp = def.split('-'); y = +dp[0]; m = +dp[1];
      }
    }
    _curYear = y; _curMonth = m;

    /* position below input */
    var rect = input.getBoundingClientRect();
    _panel.style.display = 'block';
    var panelW = 252;
    var left = rect.left + window.scrollX;
    if (left + panelW > window.innerWidth - 8) left = window.innerWidth - panelW - 8;
    _panel.style.top  = (rect.bottom + window.scrollY + 4) + 'px';
    _panel.style.left = left + 'px';

    _load(y, m);
  }

  function _hide() {
    if (_panel) _panel.style.display = 'none';
  }

  function _nav(delta) {
    var m = _curMonth + delta, y = _curYear;
    if (m < 1) { m = 12; y--; }
    if (m > 12) { m = 1; y++; }
    _curYear = y; _curMonth = m;
    _load(y, m);
  }

  function _load(year, month) {
    var grid  = _panel.querySelector('#bsdp-grid');
    var title = _panel.querySelector('#bsdp-title');
    var load  = _panel.querySelector('#bsdp-load');
    var token = ++_loadingToken;

    grid.style.display = 'none';
    load.style.display = 'block';
    load.textContent   = 'Loading…';

    fetch('/api/bs-month-info?year=' + year + '&month=' + month)
      .then(function (r) { return r.json(); })
      .then(function (info) {
        if (token !== _loadingToken) return;
        load.style.display = 'none';
        grid.style.display = '';

        title.textContent = info.month_name + ' ' + year;

        /* selected day in this month */
        var val = _curInput ? _curInput.value : '';
        var selDay = 0;
        if (/^\d{4}-\d{2}-\d{2}$/.test(val)) {
          var vp = val.split('-');
          if (+vp[0] === year && +vp[1] === month) selDay = +vp[2];
        }

        /* build grid html */
        var cells = '<div class="bsdp-row">';
        DAY_HDR.forEach(function (d) {
          cells += '<div class="bsdp-hdr' + (d === 'Sa' ? ' bsdp-sat' : '') + '">' + d + '</div>';
        });
        cells += '</div><div class="bsdp-row bsdp-days">';

        /* blank offset cells */
        for (var i = 0; i < info.first_weekday; i++) cells += '<div></div>';

        for (var d = 1; d <= info.days; d++) {
          var col = (info.first_weekday + d - 1) % 7;
          var cls = 'bsdp-day' +
            (col === 6 ? ' bsdp-sat' : '') +
            (d === selDay ? ' bsdp-sel' : '');
          cells += '<div class="' + cls + '" data-day="' + d + '">' + d + '</div>';
        }
        cells += '</div>';
        grid.innerHTML = cells;

        /* day click handler */
        grid.querySelectorAll('.bsdp-day').forEach(function (cell) {
          cell.addEventListener('click', function (e) {
            e.preventDefault(); e.stopPropagation();
            _pick(+this.dataset.day);
          });
        });
      })
      .catch(function () {
        if (token !== _loadingToken) return;
        load.textContent = 'Error loading calendar.';
      });
  }

  function _pad(n) { return String(n).padStart(2, '0'); }

  function _pick(day) {
    if (!_curInput) return;
    var bsVal = _curYear + '-' + _pad(_curMonth) + '-' + _pad(day);
    _curInput.value = bsVal;
    _hide();

    /* convert to AD and push to paired input */
    var adName = _curInput.dataset.adTarget;
    if (adName) {
      var adEl = document.getElementById(adName) ||
                 document.querySelector('[name="' + adName + '"]');
      if (adEl) {
        fetch('/api/bs-to-ad?date=' + bsVal)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.ad) {
              adEl.value = data.ad;
              _updateSmall(adEl, bsVal + ' BS');
            }
          });
      }
    }
    _curInput.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function _updateSmall(el, txt) {
    var s = el.parentElement && el.parentElement.querySelector('small.muted');
    if (s) s.textContent = txt;
  }

  /* ── wire AD → BS auto-conversion ────────────────────────────── */
  function _wireAD(adInput) {
    adInput.addEventListener('change', function () {
      var bsName = this.dataset.bsTarget;
      if (!bsName || !this.value) return;
      var bsEl = document.getElementById(bsName) ||
                 document.querySelector('[name="' + bsName + '"]');
      var adVal = this.value;
      if (bsEl) {
        fetch('/api/ad-to-bs?date=' + adVal)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.bs) {
              bsEl.value = data.bs;
              _updateSmall(bsEl.closest('label'), data.bs + ' BS');
            }
          });
      }
    });
  }

  /* ── init ─────────────────────────────────────────────────────── */
  function _init() {
    document.querySelectorAll('.bsdp').forEach(function (input) {
      input.style.cursor = 'pointer';
      input.autocomplete = 'off';
      input.addEventListener('click', function (e) {
        e.stopPropagation();
        if (_curInput === this && _panel && _panel.style.display !== 'none') {
          _hide();
        } else {
          _show(this);
        }
      });
    });

    document.querySelectorAll('[data-bs-target]').forEach(_wireAD);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }
})();
