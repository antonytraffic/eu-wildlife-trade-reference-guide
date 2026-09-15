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
        window.scrollTo({ top: 0, behavior: 'smooth' });
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

  function initReferenceGuideMegaMenuHeight() {
    /* ECL's own MegaMenu script measures the "info" and "mega" panels on
       open and keeps an inline height synced on both so they render as
       one attached panel (see the CSS overrides scoped to
       #ecl-mega-menu-item-main-2 near .ecl-mega-menu__wrapper in
       style.css for why this item needs a single-column list instead of
       ECL's default 3-column split). With the list forced into a single
       column, that measurement comes out roughly one row short of the
       list's real height, clipping the last link or two -- and ECL keeps
       re-measuring and re-applying its own short value even after this
       script corrects it (confirmed live: a plain element.style.height
       write here was silently overwritten again moments later), so this
       writes to the --rg-mega-height custom property instead of the
       height style directly. The CSS above reads height from that
       variable with !important, which permanently outranks whatever
       plain (non-important) inline height ECL's script keeps re-writing,
       so main.js only needs to keep the variable's value correct.
       sublist.scrollHeight reliably reports the list's true full height
       regardless of ECL's own miscalculation (confirmed live).
       Listens on document (capture phase) rather than the link itself,
       and re-queries the panel elements on every click rather than
       capturing them once: ECL's own toggle handler calls
       stopPropagation() on this same click (needed for its "click
       outside closes the menu" behaviour), so a bubble-phase document
       listener never even receives the event -- confirmed live, a bubble
       listener saw zero clicks while a capture listener saw exactly one
       per click. Capture fires on the way down, before that
       stopPropagation() takes effect during bubbling. */
    document.addEventListener('click', function (e) {
      if (!(e.target.closest && e.target.closest('#ecl-mega-menu-item-main-2-link'))) return;
      /* setTimeout, not requestAnimationFrame: rAF callbacks are
         suspended while the tab/pane isn't actively being painted (e.g.
         a backgrounded preview pane), and never ran there in testing --
         a macrotask timeout runs regardless and still comes safely after
         ECL's own synchronous click handling above. */
      setTimeout(function () {
        if (window.innerWidth < 1140) return; // matches the CSS override's own breakpoint
        var link = document.getElementById('ecl-mega-menu-item-main-2-link');
        var mega = document.querySelector('#ecl-mega-menu-item-main-2 .ecl-mega-menu__mega');
        var info = document.querySelector('#ecl-mega-menu-item-main-2 .ecl-mega-menu__info');
        var sublist = mega && mega.querySelector('.ecl-mega-menu__sublist');
        if (!link || !mega || !info || !sublist) return;
        if (link.getAttribute('aria-expanded') !== 'true') return;
        var h = sublist.scrollHeight + 'px';
        mega.style.setProperty('--rg-mega-height', h);
        info.style.setProperty('--rg-mega-height', h);
      }, 50);
    }, true);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSidebarHighlight();
    initBackToTop();
    initFootnotesExpand();
    initTopNavDropdown();
    initReferenceGuideMegaMenuHeight();
  });
}());
