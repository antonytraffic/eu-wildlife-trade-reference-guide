/* main.js -- sidebar highlight + scroll utilities */
(function () {
  'use strict';

  function initSidebarHighlight() {
    var nav = document.querySelector('.sidebar__nav');
    if (!nav) return;
    var links = Array.from(nav.querySelectorAll('a[href^="#"]'));
    if (links.length <= 1) return; // nothing to distinguish -- leave unselected
    var targets = links.map(function (l) {
      return document.getElementById(l.getAttribute('href').slice(1));
    }).filter(Boolean);
    if (!targets.length) return;

    var current = 0;

    var ignoreUntil = 0;

    function setActive(idx) {
      current = idx;
      links.forEach(function (l) { l.classList.remove('is-active'); });
      if (links[current]) links[current].classList.add('is-active');
    }

    links.forEach(function (l, idx) {
      l.addEventListener('click', function () {
        ignoreUntil = Date.now() + 1000;
        setActive(idx);
      });
    });

    var io = new IntersectionObserver(function (entries) {
      if (Date.now() < ignoreUntil) return;
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          var idx = targets.indexOf(e.target);
          if (idx !== -1) setActive(idx);
        }
      });
    }, { rootMargin: '-10% 0px -80% 0px', threshold: 0 });

    targets.forEach(function (t) { io.observe(t); });
  }

  function initBackToTop() {
    document.querySelectorAll('a[href="#top"]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        window.scrollTo(0, 0);
        var el = document.getElementById('top');
        if (el) el.focus({ preventScroll: true });
      });
    });
  }

  function initFootnotesExpand() {
    document.querySelectorAll('.footnotes-show-more').forEach(function (btn) {
      var overflow = btn.parentElement.querySelector('.footnotes-overflow');
      if (!overflow) return;
      var moreText = btn.textContent;
      btn.addEventListener('click', function () {
        if (overflow.hidden) {
          overflow.hidden = false;
          btn.textContent = 'Show fewer footnotes';
        } else {
          overflow.hidden = true;
          btn.textContent = moreText;
        }
      });
    });
  }

  function initTopNavDropdown() {
    var item = document.querySelector('.top-nav__item--dropdown');
    if (!item) return;
    var toggle = item.querySelector('.top-nav__dropdown-toggle');
    if (!toggle) return;

    function close() {
      item.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
    }
    function open() {
      item.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
    }

    toggle.addEventListener('click', function (e) {
      e.stopPropagation();
      if (item.classList.contains('is-open')) close(); else open();
    });
    document.addEventListener('click', function (e) {
      if (!item.contains(e.target)) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        close();
        toggle.focus();
      }
    });
  }

  /* Arriving from a search result (?hl=word word ...): highlight those words
     in the article, with a floating bar to step through the matches or clear
     them. search.js supplies every form of each search word that occurs in
     the guide ("permit permits permitted"), so no matching rules live here --
     just whole-word, case-insensitive matches. */
  function initSearchHighlight() {
    var params = new URLSearchParams(location.search);
    var article = document.querySelector('.article-body');
    if (!params.get('hl') || !article) return;
    var words = params.get('hl').split(/\s+/).filter(Boolean).slice(0, 40)
      .sort(function (a, b) { return b.length - a.length; })
      .map(function (w) { return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); });
    if (!words.length) return;
    var re = new RegExp('(^|[^\\p{L}\\p{N}])(' + words.join('|') + ')(?=[^\\p{L}\\p{N}]|$)', 'giu');

    var walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        return n.parentNode.closest('script, style, svg, mark') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    var marks = [];
    nodes.forEach(function (node) {
      var text = node.nodeValue, last = 0, frag = null, m;
      re.lastIndex = 0;
      while ((m = re.exec(text))) {
        var start = m.index + m[1].length, end = start + m[2].length;
        frag = frag || document.createDocumentFragment();
        frag.appendChild(document.createTextNode(text.slice(last, start)));
        var mark = document.createElement('mark');
        mark.className = 'search-hl';
        mark.textContent = text.slice(start, end);
        frag.appendChild(mark);
        marks.push(mark);
        last = end;
      }
      if (frag) {
        frag.appendChild(document.createTextNode(text.slice(last)));
        node.parentNode.replaceChild(frag, node);
      }
    });
    if (!marks.length) return;

    var bar = document.createElement('div');
    bar.className = 'search-hl-bar';
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', 'Search highlights');
    bar.innerHTML =
      '<span class="search-hl-bar__count" aria-live="polite"></span>' +
      '<button type="button" class="search-hl-bar__step" data-step="-1" aria-label="Previous match">&lsaquo;</button>' +
      '<button type="button" class="search-hl-bar__step" data-step="1" aria-label="Next match">&rsaquo;</button>' +
      '<button type="button" class="search-hl-bar__clear">Clear highlights</button>';
    var count = bar.querySelector('.search-hl-bar__count');
    count.textContent = marks.length + (marks.length === 1 ? ' match' : ' matches');
    var current = null;

    function step(dir) {
      // Matches inside collapsed content (e.g. hidden footnotes) are skipped.
      var shown = marks.filter(function (m) { return m.getClientRects().length; });
      if (!shown.length) return;
      var i = shown.indexOf(current);
      if (i === -1) {
        // First press: the first match at or below the top of the screen.
        i = 0;
        while (i < shown.length && shown[i].getBoundingClientRect().top < 0) i++;
        if (dir < 0) i--;
        i = (i + shown.length) % shown.length;
      } else {
        i = (i + dir + shown.length) % shown.length;
      }
      if (current) current.classList.remove('search-hl--current');
      current = shown[i];
      current.classList.add('search-hl--current');
      current.scrollIntoView({ block: 'center' });
      count.textContent = 'Match ' + (i + 1) + ' of ' + shown.length;
    }

    function clear() {
      marks.forEach(function (m) {
        var parent = m.parentNode;
        parent.replaceChild(document.createTextNode(m.textContent), m);
        parent.normalize();
      });
      bar.parentNode.removeChild(bar);
      params.delete('hl');
      var qs = params.toString();
      history.replaceState(history.state, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    }

    bar.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      if (btn.classList.contains('search-hl-bar__clear')) clear();
      else step(Number(btn.getAttribute('data-step')));
    });
    document.body.appendChild(bar);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSearchHighlight();
    initSidebarHighlight();
    initBackToTop();
    initFootnotesExpand();
    initTopNavDropdown();
  });
}());
