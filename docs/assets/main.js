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
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          var idx = targets.indexOf(e.target);
          if (idx !== -1) current = idx;
        }
      });
      links.forEach(function (l) { l.classList.remove('is-active'); });
      if (links[current]) links[current].classList.add('is-active');
    }, { rootMargin: '-15% 0px -75% 0px', threshold: 0 });

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

  document.addEventListener('DOMContentLoaded', function () {
    initSidebarHighlight();
    initBackToTop();
  });
}());
