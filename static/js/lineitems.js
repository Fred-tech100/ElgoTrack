/* Line-item editor shared by the sale (till) and purchase screens.
 * Totals shown here are a convenience only: the server recomputes and validates everything. */
(function () {
  var root = document.getElementById("editor");
  if (!root) return;
  var mode = root.dataset.mode;               // "sale" | "purchase"
  var searchUrl = root.dataset.searchUrl;
  var csrf = root.dataset.csrf;
  var decimals = parseInt(root.dataset.decimals || "0", 10);
  var canDiscount = root.dataset.canDiscount === "1";
  var lines = [];
  var token = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
  var $ = function (id) { return document.getElementById(id); };
  var fmt = function (n) { return Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }); };
  var num = function (v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; };
  var timer;

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function addProduct(p) {
    var existing = lines.find(function (l) { return l.id === p.id; });
    if (existing) { existing.qty += 1; render(); return; }
    var wholesale = $("wholesale") && $("wholesale").checked && p.wholesale !== "";
    lines.push({ id: p.id, name: p.name, unit: p.unit, stock: p.stock, tax: num(p.tax), qty: 1,
                 price: num(mode === "sale" ? (wholesale ? p.wholesale : p.price) : p.cost), discount: 0, listPrice: p });
    render();
  }

  function render() {
    var body = $("lines");
    if (!lines.length) {
      body.innerHTML = '<tr><td colspan="6" class="py-10 text-center text-slate-500">Search for a product above or scan a barcode to start.</td></tr>';
    } else {
      body.innerHTML = lines.map(function (l, i) {
        var amount = l.qty * l.price - l.discount;
        var priceLocked = mode === "sale" && !canDiscount ? "readonly" : "";
        return '<tr data-i="' + i + '"><td><div class="font-medium text-slate-900">' + esc(l.name) + '</div>' +
          (mode === "sale" ? '<div class="text-xs ' + (num(l.stock) < l.qty ? "text-red-600" : "text-slate-400") + '">' + esc(l.stock) + ' ' + esc(l.unit) + ' in stock</div>' : '') + '</td>' +
          '<td><input class="input w-20 text-right" data-f="qty" type="number" min="0" step="any" inputmode="decimal" value="' + l.qty + '"></td>' +
          '<td><input class="input w-28 text-right" data-f="price" type="number" min="0" step="any" inputmode="decimal" value="' + l.price + '" ' + priceLocked + '></td>' +
          (mode === "sale" ? '<td class="hidden sm:table-cell"><input class="input w-24 text-right" data-f="discount" type="number" min="0" step="any" inputmode="decimal" value="' + l.discount + '" ' + (canDiscount ? "" : "readonly") + '></td>' : '') +
          '<td class="num font-medium">' + fmt(amount) + '</td>' +
          '<td><button type="button" class="btn-ghost btn-sm text-red-600" data-remove="' + i + '" aria-label="Remove">✕</button></td></tr>';
      }).join("");
    }
    totals();
  }

  function totals() {
    var sub = 0, disc = 0, tax = 0;
    lines.forEach(function (l) {
      var gross = l.qty * l.price; sub += gross; disc += l.discount;
      tax += (gross - l.discount) * l.tax / 100;
    });
    var total = sub - disc + tax;
    if ($("t-sub")) $("t-sub").textContent = fmt(sub);
    if ($("t-disc")) $("t-disc").textContent = fmt(disc);
    if ($("t-tax")) $("t-tax").textContent = fmt(tax);
    $("t-total").textContent = fmt(total);
    root.dataset.total = total;
    if (mode === "sale") syncPayments(total);
  }

  function syncPayments(total) {
    var paid = 0;
    document.querySelectorAll("[data-pay]").forEach(function (el) { paid += num(el.value); });
    var credit = total - paid;
    $("t-paid").textContent = fmt(paid);
    $("t-credit").textContent = fmt(Math.max(credit, 0));
    $("t-credit-row").classList.toggle("text-red-600", credit > 0.001);
    $("pay-warning").classList.toggle("hidden", !(credit < -0.001));
  }

  function showError(msg) { var e = $("form-error"); e.textContent = msg; e.classList.toggle("hidden", !msg); if (msg) e.scrollIntoView({ block: "center" }); }

  function search(q) {
    fetch(searchUrl + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var box = $("results");
        if (!data.results.length) { box.innerHTML = '<div class="px-3 py-2 text-sm text-slate-500">No products found.</div>'; box.classList.remove("hidden"); return; }
        if (data.results.length === 1 && /^[0-9A-Za-z-]{6,}$/.test(q) && data.results[0].sku.toLowerCase() === q.toLowerCase()) { addProduct(data.results[0]); box.classList.add("hidden"); $("q").value = ""; return; }
        box.innerHTML = data.results.map(function (p, i) {
          return '<button type="button" class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-brand-50" data-pick="' + i + '"><span><span class="font-medium">' + esc(p.name) + '</span><span class="block text-xs text-slate-500">' + esc(p.sku) + '</span></span><span class="text-right text-xs text-slate-500">' + fmt(mode === "sale" ? p.price : p.cost) + '<br>' + esc(p.stock) + ' ' + esc(p.unit) + '</span></button>';
        }).join("");
        box._data = data.results; box.classList.remove("hidden");
      });
  }

  $("q").addEventListener("input", function (e) {
    clearTimeout(timer);
    var q = e.target.value.trim();
    if (!q) { $("results").classList.add("hidden"); return; }
    timer = setTimeout(function () { search(q); }, 200);
  });
  $("q").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); var first = $("results").querySelector("[data-pick]"); if (first) first.click(); else search(e.target.value.trim()); }
  });
  $("results").addEventListener("click", function (e) {
    var b = e.target.closest("[data-pick]"); if (!b) return;
    addProduct($("results")._data[parseInt(b.dataset.pick, 10)]);
    $("results").classList.add("hidden"); $("q").value = ""; $("q").focus();
  });
  $("lines").addEventListener("input", function (e) {
    var tr = e.target.closest("tr"); if (!tr || !e.target.dataset.f) return;
    lines[parseInt(tr.dataset.i, 10)][e.target.dataset.f] = num(e.target.value);
    var l = lines[parseInt(tr.dataset.i, 10)];
    tr.querySelector(".num").textContent = fmt(l.qty * l.price - l.discount);
    totals();
  });
  $("lines").addEventListener("click", function (e) {
    var b = e.target.closest("[data-remove]"); if (!b) return;
    lines.splice(parseInt(b.dataset.remove, 10), 1); render();
  });
  document.querySelectorAll("[data-pay]").forEach(function (el) { el.addEventListener("input", function () { syncPayments(num(root.dataset.total)); }); });
  if ($("pay-full")) $("pay-full").addEventListener("click", function () {
    var first = document.querySelector("[data-pay]"); if (!first) return;
    document.querySelectorAll("[data-pay]").forEach(function (el) { el.value = ""; });
    first.value = Math.round(num(root.dataset.total) * 100) / 100; syncPayments(num(root.dataset.total));
  });
  if ($("wholesale")) $("wholesale").addEventListener("change", function () {
    lines.forEach(function (l) { var p = l.listPrice; l.price = num(this.checked && p.wholesale !== "" ? p.wholesale : p.price); }, this); render();
  });

  $("submit").addEventListener("click", function () {
    showError("");
    if (!lines.length) { showError("Add at least one product."); return; }
    var payload = {
      items: lines.map(function (l) { return { product_id: l.id, quantity: String(l.qty), unit_price: String(l.price), unit_cost: String(l.price), discount: String(l.discount) }; }),
      notes: $("notes") ? $("notes").value : "",
      client_token: token
    };
    if (mode === "sale") {
      payload.customer_id = $("customer").value || null;
      payload.due_date = $("due_date") ? $("due_date").value : "";
      payload.payments = Array.prototype.map.call(document.querySelectorAll("[data-pay]"), function (el) {
        return { method_id: el.dataset.pay, amount: el.value || "0", reference: "" };
      }).filter(function (p) { return num(p.amount) > 0; });
    } else {
      payload.supplier_id = $("supplier").value;
      payload.order_date = $("order_date").value;
      payload.expected_date = $("expected_date").value;
      payload.supplier_reference = $("supplier_reference").value;
      payload.receive_now = $("receive_now") ? $("receive_now").checked : false;
    }
    var btn = $("submit"); btn.disabled = true;
    fetch(root.dataset.postUrl, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) { if (res.ok && res.d.ok) { window.location = res.d.redirect; } else { btn.disabled = false; showError(res.d.error || "Something went wrong."); } })
      .catch(function () { btn.disabled = false; showError("Network error. Please try again."); });
  });
  render();
  $("q").focus();
})();
