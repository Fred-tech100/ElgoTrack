/* Landing page: hero slider + mobile nav.
   External file only — CSP blocks inline scripts (script-src 'self'). */
(function () {
  "use strict";

  /* ---------- Mobile nav ---------- */
  var navToggle = document.getElementById("landing-nav-toggle");
  var navMenu = document.getElementById("landing-nav-menu");
  if (navToggle && navMenu) {
    navToggle.addEventListener("click", function () {
      var open = navMenu.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
      navToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    });
    navMenu.addEventListener("click", function (e) {
      if (e.target.tagName === "A" && window.matchMedia("(max-width: 767px)").matches) {
        navMenu.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.setAttribute("aria-label", "Open menu");
      }
    });
  }

  /* ---------- Hero slider ---------- */
  var slider = document.getElementById("landing-slider");
  if (!slider) return;

  var slides = slider.querySelectorAll(".landing-slide");
  var dots = slider.querySelectorAll(".landing-dot");
  var prevBtn = document.getElementById("landing-slider-prev");
  var nextBtn = document.getElementById("landing-slider-next");
  if (!slides.length) return;

  var current = 0;
  var autoplayMs = 6000;
  var timer = null;
  var paused = false;

  function goTo(index) {
    if (index < 0) index = slides.length - 1;
    if (index >= slides.length) index = 0;
    current = index;
    for (var i = 0; i < slides.length; i++) {
      var on = i === current;
      slides[i].classList.toggle("is-active", on);
      slides[i].setAttribute("aria-hidden", on ? "false" : "true");
      if (dots[i]) {
        dots[i].classList.toggle("is-active", on);
        dots[i].setAttribute("aria-selected", on ? "true" : "false");
      }
    }
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  function startAutoplay() {
    stopAutoplay();
    if (paused) return;
    timer = window.setInterval(next, autoplayMs);
  }
  function stopAutoplay() {
    if (timer) { window.clearInterval(timer); timer = null; }
  }
  function pause() { paused = true; stopAutoplay(); }
  function resume() { paused = false; startAutoplay(); }

  if (prevBtn) prevBtn.addEventListener("click", function () { prev(); startAutoplay(); });
  if (nextBtn) nextBtn.addEventListener("click", function () { next(); startAutoplay(); });

  for (var d = 0; d < dots.length; d++) {
    (function (index) {
      dots[index].addEventListener("click", function () { goTo(index); startAutoplay(); });
    })(d);
  }

  slider.addEventListener("mouseenter", pause);
  slider.addEventListener("mouseleave", resume);
  slider.addEventListener("focusin", pause);
  slider.addEventListener("focusout", function (e) {
    if (!slider.contains(e.relatedTarget)) resume();
  });

  slider.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft") { e.preventDefault(); prev(); startAutoplay(); }
    if (e.key === "ArrowRight") { e.preventDefault(); next(); startAutoplay(); }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopAutoplay(); else startAutoplay();
  });

  var prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!prefersReduced) startAutoplay();
})();