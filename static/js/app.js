(function () {
  var sidebar = document.getElementById("sidebar");
  var backdrop = document.getElementById("nav-backdrop");
  var toggle = document.getElementById("nav-toggle");
  function setNav(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("-translate-x-full", !open);
    if (backdrop) backdrop.classList.toggle("hidden", !open);
    document.body.classList.toggle("overflow-hidden", open && window.matchMedia("(max-width: 1023px)").matches);
  }
  if (toggle) toggle.addEventListener("click", function () { setNav(sidebar.classList.contains("-translate-x-full")); });
  if (backdrop) backdrop.addEventListener("click", function () { setNav(false); });

  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-print]");
    if (el) { window.print(); return; }
    el = e.target.closest("[data-back]");
    if (el) { window.history.back(); return; }
    el = e.target.closest("[data-confirm-click]");
    if (el && !window.confirm(el.getAttribute("data-confirm-click"))) e.preventDefault();
  });

  // Show / hide password (eye button) and live "passwords match" hint
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-toggle-password]");
    if (!btn) return;
    var input = document.getElementById(btn.getAttribute("data-toggle-password"));
    if (!input) return;
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-pressed", show ? "true" : "false");
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    btn.querySelector("[data-eye-open]").classList.toggle("hidden", show);
    btn.querySelector("[data-eye-closed]").classList.toggle("hidden", !show);
  });
  (function () {
    var pairs = [["id_password", "id_password_confirm"], ["id_new_password1", "id_new_password2"]];
    pairs.forEach(function (ids) {
      var a = document.getElementById(ids[0]), b = document.getElementById(ids[1]);
      if (!a || !b) return;
      var hint = document.createElement("p");
      hint.className = "help"; hint.setAttribute("aria-live", "polite");
      b.closest("div.relative").insertAdjacentElement("afterend", hint);
      function check() {
        if (!b.value) { hint.textContent = ""; return; }
        var ok = a.value === b.value;
        hint.textContent = ok ? "Passwords match." : "Passwords do not match yet.";
        hint.className = "mt-1 text-xs font-medium " + (ok ? "text-emerald-600" : "text-red-600");
      }
      a.addEventListener("input", check); b.addEventListener("input", check);
    });
  })();

  // <form data-confirm="Are you sure?">  and  <form data-prompt="Reason?" data-prompt-field="reason">
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    var ask = form.getAttribute("data-prompt");
    if (ask) {
      var answer = window.prompt(ask);
      if (!answer || !answer.trim()) { e.preventDefault(); return; }
      form.elements[form.getAttribute("data-prompt-field")].value = answer.trim();
    }
  });

  // Press "/" anywhere to jump to search
  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "/" && tag !== "input" && tag !== "textarea" && tag !== "select") {
      var s = document.getElementById("global-search");
      if (s) { e.preventDefault(); s.focus(); }
    }
  });
})();
