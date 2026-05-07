/* search.js -- client-side full-text search */
(function () {
  'use strict';

  var INDEX_URL = 'search_index.json';
  var index = null;

  function qs(name) {
    return new URLSearchParams(window.location.search).get(name) || '';
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function score(item, q) {
    if (!q) return 0;
    var ql = q.toLowerCase();
    var tl = (item.title || '').toLowerCase();
    var sl = (item.summary || '').toLowerCase();
    var bl = (item.body || '').toLowerCase();
    var s = 0;
    if (tl === ql)             s += 100;
    else if (tl.startsWith(ql)) s += 75;
    else if (tl.includes(ql))   s += 50;
    if (sl.includes(ql)) s += 20;
    var hits = (bl.match(new RegExp(ql.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length;
    s += Math.min(hits * 2, 30);
    return s;
  }

  function excerpt(body, q, max) {
    max = max || 220;
    if (!body) return '';
    var ql = q.toLowerCase();
    var idx = body.toLowerCase().indexOf(ql);
    if (idx === -1) return esc(body.slice(0, max)) + (body.length > max ? '&hellip;' : '');
    var s = Math.max(0, idx - 80);
    var e = Math.min(body.length, idx + q.length + 120);
    var out = (s > 0 ? '&hellip;' : '') + esc(body.slice(s, e)) + (e < body.length ? '&hellip;' : '');
    var safe = esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return out.replace(new RegExp('(' + safe + ')', 'gi'), '<mark>$1</mark>');
  }

  function urlForItem(item) {
    if (item.parent) return 'chapters/' + esc(item.slug) + '.html';
    return 'chapters/' + esc(item.slug) + '.html';
  }

  function render(results, q) {
    var container = document.getElementById('search-results');
    var countEl   = document.getElementById('search-count');
    if (!container) return;
    if (!q) { container.innerHTML = ''; if (countEl) countEl.textContent = ''; return; }
    if (!results.length) {
      if (countEl) countEl.textContent = '0 results';
      container.innerHTML = '<div class="no-results"><p>No results found for <strong>' +
        esc(q) + '</strong>.</p><p>Try different terms or <a href="index.html">browse sections</a>.</p></div>';
      return;
    }
    if (countEl) countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '');
    container.innerHTML = results.map(function (item) {
      var meta = item.section_number ? 'Section ' + esc(item.section_number) : '';
      if (item.parent) meta = 'Sub-section';
      return '<div class="search-result">' +
        '<div class="search-result__title"><a href="' + urlForItem(item) + '">' + esc(item.title) + '</a></div>' +
        (meta ? '<div class="search-result__meta">' + meta + '</div>' : '') +
        (item.summary ? '<p class="search-result__snippet">' + esc(item.summary) + '</p>' : '') +
        '<p class="search-result__snippet">' + excerpt(item.body, q) + '</p>' +
        '</div>';
    }).join('');
  }

  function search(q) {
    if (!index) return [];
    return index.map(function (item) {
      return Object.assign({}, item, { _score: score(item, q) });
    }).filter(function (i) { return i._score > 0; })
      .sort(function (a, b) { return b._score - a._score; });
  }

  function init() {
    var input     = document.getElementById('search-input');
    var container = document.getElementById('search-results');
    if (!input || !container) return;

    var initial = qs('q');
    if (initial) input.value = initial;

    fetch(INDEX_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        index = data;
        if (initial) render(search(initial), initial);
      })
      .catch(function () {
        container.innerHTML = '<p>Search is temporarily unavailable.</p>';
      });

    var timer;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = input.value.trim();
        var url = new URL(window.location);
        if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
        window.history.replaceState({}, '', url);
        render(search(q), q);
      }, 200);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
}());
