/* search.js -- client-side full-text search over search_index.json.
   The index holds one entry per page, split into heading-level sections
   (see build_search_index), so a result can link straight to the heading
   it matched. Matching is word-based: every search word must appear in a
   section (in any order, anywhere in it or its page title); words are cut
   to a rough stem so "permits"/"permit" and "trophies"/"trophy" match;
   hyphenated words match with or without the hyphen; "quoted phrases" must
   appear exactly. Misspelt words get a "did you mean" suggestion, and a
   search no section fully matches falls back to partial matches.
   Loaded on every page: besides the search page itself, it adds
   as-you-type suggestions to the header and homepage search boxes. */
(function () {
  'use strict';

  // Site root, from this script's own URL (assets/search.js), so the index
  // and result links resolve the same from any page depth.
  var ROOT = new URL('../', (document.currentScript && document.currentScript.src) || location.href);
  var INDEX_URL = new URL('search_index.json?v=0f5c3668d7', ROOT).href;
  var SUGGESTIONS = 6;
  var SECTIONS_PER_RESULT = 3;
  var SNIPPET_WORDS = 34;
  var EXAMPLES = ['hunting trophies', 'captive-bred', 'personal effects', 'caviar', 'import permit', '"Annex A"'];
  var STOPWORDS = {};
  ('a an and are as at be been but by can could do does for from has have how i if in into is it its ' +
   'may me my no not of on or our should so than that the their them then there these they this those ' +
   'to was we what when where which who why will with would you your').split(' ')
    .forEach(function (w) { STOPWORDS[w] = true; });

  var TOKEN_RE = /[\p{L}\p{N}]+(?:['’][\p{L}]+)*(?:[-./][\p{L}\p{N}]+)*/gu;

  var pages = null;    // index entries, prepared by prepare()
  var sections = [];   // every page's sections, flattened
  var vocab = {};      // key -> { df: sections containing it, forms: { surface word: count } }

  /* -- Text processing ----------------------------------------------------- */

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fold(s) {
    return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/['’]/g, '');
  }

  /* Rough English stemmer: enough to make singular/plural and common verb
     endings meet at the same key. Applied identically to the guide's text
     and to the query, so it only has to be consistent, not linguistically
     right ("trade", "traded", "trading" all become "trad"). */
  function stem(w) {
    if (w.length < 4 || /[0-9]/.test(w)) return w;
    if (/ies$/.test(w) && w.length > 4) w = w.slice(0, -3) + 'y';
    else if (/(xes|ches|shes|sses|zes)$/.test(w)) w = w.slice(0, -2);
    else if (/[^sui]s$/.test(w)) w = w.slice(0, -1);
    var m;
    if (/ied$/.test(w)) w = w.slice(0, -3) + 'y';
    else if (/eed$/.test(w)) { if (w.length > 4) w = w.slice(0, -1); }
    else if ((m = /^(.+?)(ed|ing)$/.exec(w)) && m[1].length >= 2 && /[aeiouy]/.test(m[1])) w = m[1];
    if (w.length >= 4 && /([b-df-hj-np-tv-z])\1$/.test(w)) w = w.slice(0, -1);
    if (w.length >= 3 && /e$/.test(w)) w = w.slice(0, -1);
    return w;
  }

  /* A matched word -> its search keys. "re-export" gives "re", "export" and
     "reexport"; "338/97" and "3.3.1" are kept whole as well as split. */
  function keysFor(token) {
    var f = fold(token);
    var parts = f.split(/[-./]/).filter(Boolean);
    if (parts.length === 1) return [stem(f)];
    var keys = parts.map(stem);
    var joined = f.replace(/-/g, '');
    keys.push(/[./]/.test(joined) ? joined : stem(joined));
    return keys;
  }

  function tokenize(text) {
    var out = [], m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(text))) out.push({ text: m[0], start: m.index, end: m.index + m[0].length });
    return out;
  }

  function keySet(text) {
    var set = {};
    tokenize(text).forEach(function (t) { keysFor(t.text).forEach(function (k) { set[k] = true; }); });
    return set;
  }

  /* Lower-case, unaccented, punctuation-free text with single spaces --
     used for exact-phrase checks, so "re-export" reads as "re export". */
  function flat(text) {
    return ' ' + fold(text).replace(/[^\p{L}\p{N}]+/gu, ' ').trim() + ' ';
  }

  /* -- Index preparation (once, on load) -------------------------------- */

  function prepare(data) {
    var totalLen = 0;
    data.forEach(function (page, pi) {
      page.titleKeys = keySet(page.title);
      page.summaryKeys = keySet(page.summary || '');
      var lastByLevel = {};
      page.sections.forEach(function (sec) {
        sec.page = page;
        sec.pageIndex = pi;
        sec.counts = {};
        sec.len = 0;
        tokenize(sec.x).forEach(function (t) {
          var form = t.text.toLowerCase();
          keysFor(t.text).forEach(function (k) {
            sec.counts[k] = (sec.counts[k] || 0) + 1;
            var v = vocab[k] || (vocab[k] = { df: 0, forms: {}, _last: -1 });
            v.forms[form] = (v.forms[form] || 0) + 1;
          });
          sec.len++;
        });
        sec.headKeys = keySet(sec.h);
        sec.flatText = flat(sec.h + ' ' + sec.x);
        sec.flatHead = flat(sec.h);
        sec.flatHeadBare = flat(sec.h.replace(/^[\d.\s]+/, ''));
        // Nearest enclosing heading, shown as context for h3/h4 sections.
        lastByLevel[sec.l] = sec;
        for (var l = sec.l - 1; l >= 2; l--) {
          if (lastByLevel[l]) { sec.parentHeading = lastByLevel[l].h; break; }
        }
        Object.keys(sec.counts).concat(Object.keys(sec.headKeys)).forEach(function (k) {
          var v = vocab[k] || (vocab[k] = { df: 0, forms: {}, _last: -1 });
          if (v._last !== sections.length) { v.df++; v._last = sections.length; }
        });
        totalLen += sec.len;
        sections.push(sec);
      });
    });
    prepare.avgLen = totalLen / Math.max(1, sections.length);
    return data;
  }

  /* -- Query parsing ------------------------------------------------------ */

  /* typing: the box is live (suggestions), so the last word is always
     treated as possibly unfinished, even when it is a word on its own. */
  function parseQuery(raw, typing) {
    var phrases = [];
    var text = raw.replace(/"([^"]+)"/g, function (_, p) {
      if (p.trim()) phrases.push(flat(p));
      return ' ' + p + ' ';
    }).replace(/"/g, ' ');
    var tokens = tokenize(text);
    var meaningful = tokens.filter(function (t) { return !STOPWORDS[fold(t.text)]; });
    if (!meaningful.length) meaningful = tokens;
    // Still typing the last word? Let it match longer words it begins.
    var lastIsOpen = /[\p{L}\p{N}]$/u.test(raw);
    var terms = meaningful.map(function (t, i) {
      var keys = {};
      var main = keysFor(t.text);
      var compound = main[main.length - 1];
      keys[compound] = 1;
      var known = !!vocab[compound];
      var isLast = i === meaningful.length - 1 && t === tokens[tokens.length - 1];
      if ((!known || typing) && isLast && lastIsOpen && compound.length >= 3 && !/[0-9]/.test(compound)) {
        var prefix = fold(t.text).replace(/-/g, '');
        Object.keys(vocab).forEach(function (k) {
          if (k.lastIndexOf(prefix, 0) === 0 && k !== compound) { keys[k] = 0.5; known = true; }
        });
      }
      return { raw: t.text, keys: keys, known: known };
    });
    var words = tokens.map(function (t) { return fold(t.text).replace(/[^\p{L}\p{N}]+/gu, ' '); });
    var pairs = [];
    for (var i = 0; i + 1 < words.length; i++) pairs.push(' ' + words[i] + ' ' + words[i + 1] + ' ');
    return {
      raw: raw, terms: terms, phrases: phrases, pairs: pairs,
      whole: words.length > 1 ? ' ' + words.join(' ') + ' ' : '',
      exact: flat(words.join(' '))
    };
  }

  /* -- Scoring ------------------------------------------------------------ */

  function idf(term) {
    var df = 0;
    Object.keys(term.keys).forEach(function (k) { if (vocab[k]) df = Math.max(df, vocab[k].df); });
    var n = sections.length;
    return Math.log(1 + (n - df + 0.5) / (df + 0.5));
  }

  /* BM25-style score for one section: word frequency (damped, adjusted
     for section length) plus bonuses for the word being in the section's
     heading or the page title, and for the words appearing together. */
  function scoreSection(sec, q, idfs) {
    var score = 0, matched = 0, inContent = false;
    var norm = 1 - 0.75 + 0.75 * (sec.len / prepare.avgLen);
    q.terms.forEach(function (term, ti) {
      var tf = 0, head = false, title = false, summary = false;
      Object.keys(term.keys).forEach(function (k) {
        var w = term.keys[k];
        tf += (sec.counts[k] || 0) * w;
        if (sec.headKeys[k]) head = true;
        if (sec.page.titleKeys[k]) title = true;
        if (sec.page.summaryKeys[k]) summary = true;
      });
      if (tf > 0 || head) inContent = true;
      if (tf > 0 || head || title) matched++;
      var tfPart = tf > 0 ? (tf * 2.2) / (tf + 1.2 * norm) : 0;
      score += idfs[ti] * (tfPart + (head ? 2.5 : 0) + (title ? 1.5 : 0) + (summary ? 0.3 : 0));
    });
    for (var i = 0; i < q.phrases.length; i++) {
      if (sec.flatText.indexOf(q.phrases[i]) === -1) return null;   // quoted phrase is required
      if (sec.flatHead.indexOf(q.phrases[i]) !== -1) score += 4;
    }
    if (q.whole && sec.flatText.indexOf(q.whole) !== -1) score += 3;
    if (q.whole && sec.flatHead.indexOf(q.whole) !== -1) score += 4;
    if (sec.flatHeadBare === q.exact) score += 6;   // heading is exactly the search
    q.pairs.forEach(function (p) { if (sec.flatText.indexOf(p) !== -1) score += 0.75; });
    return { sec: sec, score: score, matched: matched, inContent: inContent };
  }

  /* All matching pages, best first, each with its best sections. With
     requireAll=false a section only needs one of the search words. */
  function run(q, requireAll) {
    if (!q.terms.length) return [];
    var idfs = q.terms.map(idf);
    var need = requireAll ? q.terms.length : 1;
    var byPage = {};
    sections.forEach(function (sec) {
      var r = scoreSection(sec, q, idfs);
      if (!r || r.matched < need) return;
      if (!requireAll) r.score *= r.matched / q.terms.length;
      (byPage[sec.pageIndex] || (byPage[sec.pageIndex] = [])).push(r);
    });
    return Object.keys(byPage).map(function (pi) {
      var hits = byPage[pi].sort(function (a, b) { return b.score - a.score; });
      var score = hits[0].score + 0.3 * (hits[1] ? hits[1].score : 0) + 0.15 * (hits[2] ? hits[2].score : 0);
      return {
        page: pages[pi], score: score,
        shown: hits.filter(function (h) { return h.inContent; }).slice(0, SECTIONS_PER_RESULT)
      };
    }).sort(function (a, b) { return b.score - a.score; });
  }

  /* -- Spelling suggestions ------------------------------------------------ */

  function editDistance(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    var prev2 = null, prev = [], cur, i, j;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      cur = [i];
      var rowMin = i;
      for (j = 1; j <= b.length; j++) {
        var cost = a[i - 1] === b[j - 1] ? 0 : 1;
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
        if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
          cur[j] = Math.min(cur[j], prev2[j - 2] + 1);   // transposed letters
        }
        if (cur[j] < rowMin) rowMin = cur[j];
      }
      if (rowMin > max) return max + 1;
      prev2 = prev; prev = cur;
    }
    return prev[b.length];
  }

  var allForms = null;
  function suggest(word) {
    var w = fold(word);
    if (w.length < 3 || /[0-9]/.test(w)) return null;
    if (!allForms) {
      var totals = {};
      Object.keys(vocab).forEach(function (k) {
        var forms = vocab[k].forms;
        Object.keys(forms).forEach(function (f) {
          if (/^[\p{L}]{3,}$/u.test(f)) totals[f] = Math.max(totals[f] || 0, forms[f]);
        });
      });
      allForms = Object.keys(totals).map(function (f) { return { form: f, n: totals[f] }; });
    }
    var max = w.length <= 4 ? 1 : 2;
    var best = null;
    allForms.forEach(function (c) {
      var d = editDistance(w, fold(c.form), max);
      if (d > max) return;
      if (!best || d < best.d || (d === best.d && c.n > best.n)) best = { form: c.form, d: d, n: c.n };
    });
    return best && best.form;
  }

  function correctedQuery(q) {
    var out = q.raw, changed = false;
    for (var i = 0; i < q.terms.length; i++) {
      var t = q.terms[i];
      if (t.known) continue;
      var s = suggest(t.raw);
      if (!s) return null;
      out = out.replace(t.raw, s);
      changed = true;
    }
    return changed ? out : null;
  }

  /* -- Rendering ------------------------------------------------------------ */

  function isHit(token, q) {
    var keys = keysFor(token);
    for (var i = 0; i < q.terms.length; i++) {
      for (var j = 0; j < keys.length; j++) if (q.terms[i].keys[keys[j]] !== undefined) return i;
    }
    return -1;
  }

  /* Text with every search-word occurrence wrapped in <mark>. */
  function markAll(text, q) {
    var out = '', pos = 0;
    tokenize(text).forEach(function (t) {
      if (isHit(t.text, q) === -1) return;
      out += esc(text.slice(pos, t.start)) + '<mark>' + esc(t.text) + '</mark>';
      pos = t.end;
    });
    return out + esc(text.slice(pos));
  }

  /* The stretch of a section's text with the most different search words
     in it (then the most occurrences), trimmed to whole words. */
  function snippet(text, q) {
    var toks = tokenize(text);
    if (!toks.length) return '';
    var hits = toks.map(function (t) { return isHit(t.text, q); });
    var best = -1, bestStart = 0, counts = {}, distinct = 0, total = 0;
    for (var i = 0; i < toks.length; i++) {
      if (hits[i] !== -1) { if (!counts[hits[i]]) distinct++; counts[hits[i]] = (counts[hits[i]] || 0) + 1; total++; }
      var out = i - SNIPPET_WORDS;
      if (out >= 0 && hits[out] !== -1) { counts[hits[out]]--; if (!counts[hits[out]]) distinct--; total--; }
      var value = distinct * 100 + total;
      if (total && value > best) { best = value; bestStart = Math.max(0, i - SNIPPET_WORDS + 1); }
    }
    if (best === -1) bestStart = 0;
    // Pull the window back a little so the first match isn't the first word.
    var firstHit = bestStart;
    while (firstHit < toks.length && hits[firstHit] === -1) firstHit++;
    var from = Math.max(0, Math.min(bestStart, firstHit - 6));
    var to = Math.min(toks.length - 1, from + SNIPPET_WORDS - 1);
    var s = toks[from].start, e = toks[to].end;
    return (s > 0 ? '&hellip;' : '') + markAll(text.slice(s, e), q) + (e < text.length ? '&hellip;' : '');
  }

  /* Words to highlight on the destination page: every form of each matched
     search word that occurs in the guide ("permit", "permits", "permitted"),
     limited to words that occur on that page -- an unfinished word like
     "tro" would otherwise bring in every word that starts with it. */
  function onPage(page, k) {
    return page.sections.some(function (sec) { return sec.counts[k] || sec.headKeys[k]; });
  }

  function highlightParam(q, page) {
    var words = {};
    q.terms.forEach(function (term) {
      Object.keys(term.keys).forEach(function (k) {
        var v = vocab[k];
        if (!v || !onPage(page, k)) return;
        Object.keys(v.forms).sort(function (a, b) { return v.forms[b] - v.forms[a]; })
          .slice(0, 6).forEach(function (f) { words[f] = true; });
      });
    });
    var list = Object.keys(words).slice(0, 40);
    return list.length ? 'hl=' + encodeURIComponent(list.join(' ')) : '';
  }

  function pageMeta(page) {
    var bits = [];
    if (page.label) bits.push(esc(page.label));
    if (page.parent) bits.push(esc(page.parent));
    return bits.join(' &middot; ');
  }

  function renderResult(r, q) {
    var page = r.page;
    var hl = highlightParam(q, page);
    var url = page.url + (hl ? '?' + hl : '');
    var meta = pageMeta(page);
    var html = '<li class="search-result">' +
      (meta ? '<div class="search-result__meta">' + meta + '</div>' : '') +
      '<h2 class="search-result__title"><a href="' + esc(url) + '">' + markAll(page.title, q) + '</a></h2>';
    var intro = r.shown.filter(function (h) { return !h.sec.id; })[0];
    var subs = r.shown.filter(function (h) { return h.sec.id; });
    if (intro) {
      html += '<p class="search-result__snippet">' + snippet(intro.sec.x, q) + '</p>';
    } else if (!subs.length && page.summary) {
      html += '<p class="search-result__snippet">' + markAll(page.summary, q) + '</p>';
    }
    if (subs.length) {
      html += '<ul class="search-result__sections">' + subs.map(function (h) {
        var sec = h.sec;
        return '<li class="search-result__section">' +
          (sec.parentHeading ? '<span class="search-result__context">' + esc(sec.parentHeading) + ' &rsaquo;</span> ' : '') +
          '<a href="' + esc(url + '#' + sec.id) + '">' + markAll(sec.h, q) + '</a>' +
          (sec.x ? '<p class="search-result__snippet">' + snippet(sec.x, q) + '</p>' : '') +
          '</li>';
      }).join('') + '</ul>';
    }
    return html + '</li>';
  }

  function searchLink(text) {
    return '<a href="search.html?q=' + encodeURIComponent(text) + '" data-query="' + esc(text) + '">' + esc(text) + '</a>';
  }

  function examples() {
    return '<div class="search-examples"><p>Try searching for:</p><ul>' +
      EXAMPLES.map(function (e) { return '<li>' + searchLink(e) + '</li>'; }).join('') + '</ul></div>';
  }

  function render(raw) {
    var container = document.getElementById('search-results');
    var countEl = document.getElementById('search-count');
    if (!container) return;
    raw = raw.trim();
    if (!raw) { container.innerHTML = examples(); countEl.textContent = ''; return; }

    var q = parseQuery(raw);
    var results = run(q, true);
    var notice = '', shownQuery = q;

    if (!results.length) {
      var fix = correctedQuery(q);
      var fixedQ = fix && parseQuery(fix);
      var fixedResults = fixedQ ? run(fixedQ, true) : [];
      if (fixedResults.length) {
        results = fixedResults; shownQuery = fixedQ;
        notice = '<p>No results for <strong>' + esc(raw) + '</strong>. Showing results for ' +
          '<strong>' + esc(fix) + '</strong> instead.</p>';
        countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') + ' for ' + fix;
      } else {
        results = q.terms.length > 1 ? run(q, false) : [];
        var didYouMean = fix ? '<p>Did you mean ' + searchLink(fix) + '?</p>' : '';
        if (results.length) {
          notice = '<p>No pages contain all of the words in <strong>' + esc(raw) + '</strong>. ' +
            'Showing pages that contain some of them.</p>' + didYouMean;
          countEl.textContent = 'No exact matches. ' + results.length + ' partial match' +
            (results.length !== 1 ? 'es' : '');
        } else {
          countEl.textContent = '0 results';
          container.innerHTML = '<div class="no-results"><p>No results found for <strong>' + esc(raw) +
            '</strong>.</p>' + didYouMean +
            '<p>Try fewer or different words, or <a href="index.html">browse sections</a>.</p></div>' + examples();
          return;
        }
      }
    } else {
      countEl.textContent = results.length + ' result' + (results.length !== 1 ? 's' : '') + ' for ' + raw;
    }

    container.innerHTML = (notice ? '<div class="search-notice">' + notice + '</div>' : '') +
      '<ol class="search-results-list">' + results.map(function (r) { return renderResult(r, shownQuery); }).join('') + '</ol>';
  }

  /* -- Wiring ----------------------------------------------------------------- */

  var loading = null;
  function loadIndex() {
    if (!loading) {
      loading = fetch(INDEX_URL)
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (data) { pages = prepare(data); return pages; });
    }
    return loading;
  }

  function initSearchPage() {
    var input = document.getElementById('search-input');
    var container = document.getElementById('search-results');
    if (!input || !container) return;
    var form = input.form;

    var initial = new URLSearchParams(window.location.search).get('q') || '';
    if (initial) input.value = initial;

    function update() {
      var q = input.value.trim();
      var url = new URL(window.location);
      if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
      window.history.replaceState({}, '', url);
      if (pages) render(q);
    }

    loadIndex()
      .then(function () { render(input.value); })
      .catch(function () {
        container.innerHTML = '<p>Search is temporarily unavailable.</p>';
      });

    var timer;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(update, 200);
    });
    if (form) form.addEventListener('submit', function (e) {
      e.preventDefault();
      clearTimeout(timer);
      update();
    });
    // Example and "did you mean" links search in place instead of reloading.
    container.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[data-query]');
      if (!a) return;
      e.preventDefault();
      input.value = a.getAttribute('data-query');
      update();
      input.focus();
    });
  }

  /* Top matches for the suggestion list: each page's best section. */
  function suggestions(raw) {
    var q = parseQuery(raw, true);
    var items = run(q, true).slice(0, SUGGESTIONS).map(function (r) {
      var hl = highlightParam(q, r.page);
      var sec = r.shown[0] && r.shown[0].sec.id ? r.shown[0].sec : null;
      return {
        url: new URL(r.page.url, ROOT).href + (hl ? '?' + hl : '') + (sec ? '#' + sec.id : ''),
        title: sec ? sec.h : r.page.title,
        context: sec ? r.page.title : (r.page.label || r.page.parent || '')
      };
    });
    return { q: q, items: items };
  }

  /* Header / homepage search box -> ARIA combobox with a suggestion list.
     Enter with nothing chosen still submits to the search page as before,
     and if the index can't load the box simply behaves as a plain form. */
  var suggestCount = 0;
  function initSuggest(input) {
    var form = input.form;
    var id = 'search-suggest-' + (++suggestCount);
    var list = document.createElement('ul');
    list.id = id;
    list.className = 'search-suggest';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Search suggestions');
    list.hidden = true;
    var status = document.createElement('div');
    status.className = 'sr-only';
    status.setAttribute('role', 'status');
    form.classList.add('has-search-suggest');
    form.appendChild(list);
    form.appendChild(status);
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', id);
    input.setAttribute('autocomplete', 'off');

    var items = [], active = -1, timer;

    function close() {
      list.hidden = true;
      items = [];
      active = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }

    function setActive(i) {
      var opts = list.children;
      if (opts[active]) opts[active].setAttribute('aria-selected', 'false');
      active = i;
      opts[i].setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', opts[i].id);
      opts[i].scrollIntoView({ block: 'nearest' });
    }

    // Keep the list inside 16px screen gutters (it is right-aligned to the form).
    function fit() {
      list.style.transform = '';
      var r = list.getBoundingClientRect(), shift = 0;
      if (r.right > window.innerWidth - 16) shift = window.innerWidth - 16 - r.right;
      if (r.left + shift < 16) shift = 16 - r.left;
      if (shift) list.style.transform = 'translateX(' + Math.round(shift) + 'px)';
    }

    function update() {
      var raw = input.value.trim();
      if (raw.length < 2) { close(); status.textContent = ''; return; }
      loadIndex().then(function () {
        if (input.value.trim() !== raw || document.activeElement !== input) return;
        var s = suggestions(raw);
        items = s.items.concat([{ url: form.action + '?q=' + encodeURIComponent(raw), all: true }]);
        list.innerHTML = items.map(function (it, i) {
          var body = it.all
            ? 'See all results for <strong>' + esc(raw) + '</strong>'
            : '<span class="search-suggest__title">' + markAll(it.title, s.q) + '</span>' +
              (it.context ? '<span class="search-suggest__context">' + esc(it.context) + '</span>' : '');
          return '<li role="option" id="' + id + '-' + i + '" aria-selected="false" class="search-suggest__option' +
            (it.all ? ' search-suggest__all' : '') + '">' + body + '</li>';
        }).join('');
        active = -1;
        input.removeAttribute('aria-activedescendant');
        list.hidden = false;
        fit();
        input.setAttribute('aria-expanded', 'true');
        status.textContent = s.items.length
          ? s.items.length + (s.items.length === 1 ? ' suggestion' : ' suggestions') + '. Use the up and down arrows to choose.'
          : 'No suggestions. Press Enter to search.';
      }, close);
    }

    input.addEventListener('focus', function () { loadIndex().catch(function () {}); });
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(update, 120);
    });
    input.addEventListener('keydown', function (e) {
      if (list.hidden) {
        if (e.key === 'ArrowDown') { e.preventDefault(); update(); }
        return;
      }
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive(active + 1 < items.length ? active + 1 : 0); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active > 0 ? active - 1 : items.length - 1); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); location.href = items[active].url; }
    });
    input.addEventListener('blur', close);
    // mousedown would blur the input (closing the list) before the click lands.
    list.addEventListener('mousedown', function (e) { e.preventDefault(); });
    list.addEventListener('click', function (e) {
      var li = e.target.closest('[role="option"]');
      if (li) location.href = items[Array.prototype.indexOf.call(list.children, li)].url;
    });
  }

  function init() {
    initSearchPage();
    var inputs = document.querySelectorAll('form[action$="search.html"] input[name="q"]');
    Array.prototype.forEach.call(inputs, function (input) {
      if (input.id !== 'search-input') initSuggest(input);
    });
  }

  document.addEventListener('DOMContentLoaded', init);

}());
