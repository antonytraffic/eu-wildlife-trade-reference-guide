/* main.js -- sidebar highlight + scroll utilities */
(function () {
  'use strict';

  function initSidebarHighlight() {
    var nav = document.querySelector('.sidebar__nav');
    if (!nav) return;
    var links = Array.from(nav.querySelectorAll('a[href^="#"]'));
    if (!links.length) return;
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

  document.addEventListener('DOMContentLoaded', function () {
    initSidebarHighlight();
    initBackToTop();
    initFootnotesExpand();
  });
}());
