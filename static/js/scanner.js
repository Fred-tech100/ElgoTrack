/* In-app document verifier: reads a QR code from the camera (or a photo) with jsQR and asks the server who issued it.
 * All server-supplied text is inserted with textContent: nothing from the network is ever treated as HTML. */
(function () {
  var root = document.getElementById("scanner");
  if (!root || typeof jsQR === "undefined") return;
  var lookupUrl = root.dataset.lookupUrl;
  var video = document.getElementById("scan-video"), idle = document.getElementById("scan-idle"), frame = document.getElementById("scan-frame");
  var startBtn = document.getElementById("scan-start"), stopBtn = document.getElementById("scan-stop"), statusEl = document.getElementById("scan-status");
  var out = document.getElementById("scan-result");
  var canvas = document.createElement("canvas"), ctx = canvas.getContext("2d", { willReadFrequently: true });
  var stream = null, running = false, lastText = "", lastAt = 0;

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = text; return n; }
  function say(msg) { statusEl.textContent = msg || ""; }
  function money(v, cur) { var n = Number(v); return (cur ? cur + " " : "") + (isNaN(n) ? v : n.toLocaleString(undefined, { maximumFractionDigits: 2 })); }

  var THEMES = {
    genuine: { box: "border-emerald-200", head: "bg-emerald-600", title: "GENUINE", sub: "Recorded by your business and unaltered." },
    void: { box: "border-amber-300", head: "bg-amber-500", title: "NO LONGER VALID", sub: "The document exists but was cancelled or reversed." },
    tampered: { box: "border-red-200", head: "bg-red-600", title: "RECORD ALTERED", sub: "Stored figures do not match the security signature." },
    foreign: { box: "border-red-200", head: "bg-red-600", title: "NOT YOUR DOCUMENT", sub: "Issued by a different business." },
    notfound: { box: "border-red-200", head: "bg-red-600", title: "NOT FOUND: POSSIBLE FORGERY", sub: "No such document exists in the system." }
  };

  function row(label, value, strong) {
    var r = el("div", "flex justify-between gap-4 px-5 py-2.5");
    r.appendChild(el("dt", "text-slate-500", label));
    r.appendChild(el("dd", strong ? "font-semibold text-slate-900" : "text-right", value));
    return r;
  }

  function render(d) {
    var t = THEMES[d.status] || THEMES.notfound;
    var card = el("div", "overflow-hidden rounded-lg border bg-white shadow-card " + t.box);
    var head = el("div", "px-5 py-4 text-white " + t.head);
    head.appendChild(el("p", "text-lg font-bold tracking-wide", t.title));
    head.appendChild(el("p", "text-sm opacity-90", d.message || t.sub));
    card.appendChild(head);
    if (d.number) {
      var dl = el("dl", "divide-y divide-slate-100 text-sm");
      dl.appendChild(row(d.kind === "invoice" ? "Invoice" : "Receipt", d.number, true));
      dl.appendChild(row("Date", d.date));
      dl.appendChild(row("Customer", d.customer));
      dl.appendChild(row(d.kind === "invoice" ? "Invoice total" : "Amount received", money(d.amount, d.currency), true));
      if (d.kind === "invoice") {
        dl.appendChild(row("Paid so far", money(d.paid, d.currency)));
        dl.appendChild(row("Balance due", money(d.balance, d.currency)));
        dl.appendChild(row("Payment status", d.payment_status));
      } else if (d.payment_status) {
        dl.appendChild(row("Payment status", d.payment_status));
        if (d.balance !== undefined) dl.appendChild(row("Balance due", money(d.balance, d.currency)));
      }
      dl.appendChild(row("Times verified", String(d.verified_before + 1)));
      card.appendChild(dl);
      if (d.items && d.items.length) {
        var list = el("ul", "divide-y divide-slate-100 border-t border-slate-100 text-sm");
        d.items.forEach(function (i) {
          var li = el("li", "flex justify-between gap-4 px-5 py-2");
          li.appendChild(el("span", "", (i.qty ? i.qty + " × " : "") + i.name + (i.method ? " (" + i.method + ")" : "")));
          li.appendChild(el("span", "tabular-nums", money(i.total, d.currency)));
          list.appendChild(li);
        });
        card.appendChild(list);
      }
      (d.warnings || []).forEach(function (w) { card.appendChild(el("p", "border-t border-amber-200 bg-amber-50 px-5 py-2.5 text-sm text-amber-900", w)); });
      if (d.open_url && d.open_url.charAt(0) === "/") {
        var a = el("a", "link block border-t border-slate-100 px-5 py-3 text-sm", "Open the document in the system");
        a.href = d.open_url; card.appendChild(a);
      }
    }
    out.insertBefore(card, out.firstChild);
    while (out.children.length > 4) out.removeChild(out.lastChild);   // keep the last few checks on screen
  }

  function lookup(text) {
    say("Checking…");
    fetch(lookupUrl + "?q=" + encodeURIComponent(text), { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { if (r.status === 403) throw new Error("You do not have permission to verify documents."); if (!r.ok) throw new Error("The check failed. Try again."); return r.json(); })
      .then(function (d) { say(""); render(d); if (navigator.vibrate) navigator.vibrate(d.status === "genuine" ? 60 : [80, 60, 80]); })
      .catch(function (e) { say(e.message || "Network error."); });
  }

  function decode(img, w, h) {
    canvas.width = w; canvas.height = h;
    ctx.drawImage(img, 0, 0, w, h);
    var data = ctx.getImageData(0, 0, w, h);
    return jsQR(data.data, w, h, { inversionAttempts: "attemptBoth" });
  }

  function tick() {
    if (!running) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA && video.videoWidth) {
      var scale = Math.min(1, 720 / video.videoWidth);
      var code = decode(video, Math.round(video.videoWidth * scale), Math.round(video.videoHeight * scale));
      var now = Date.now();
      if (code && code.data && (code.data !== lastText || now - lastAt > 4000)) {
        lastText = code.data; lastAt = now;
        lookup(code.data);
      }
    }
    requestAnimationFrame(tick);
  }

  function stop() {
    running = false;
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    video.srcObject = null;
    idle.classList.remove("hidden"); frame.classList.add("hidden"); frame.classList.remove("flex");
    startBtn.classList.remove("hidden"); stopBtn.classList.add("hidden");
  }

  startBtn.addEventListener("click", function () {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      say("The camera needs a secure (https) connection. You can still scan from a photo or type the code."); return;
    }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
      .then(function (s) {
        stream = s; video.srcObject = s; return video.play();
      })
      .then(function () {
        running = true; lastText = "";
        idle.classList.add("hidden"); frame.classList.remove("hidden"); frame.classList.add("flex");
        startBtn.classList.add("hidden"); stopBtn.classList.remove("hidden");
        say("Point the camera at the QR code."); tick();
      })
      .catch(function () { say("Could not open the camera. Allow camera access, or scan from a photo or type the code."); });
  });
  stopBtn.addEventListener("click", function () { stop(); say(""); });
  window.addEventListener("pagehide", stop);

  document.getElementById("scan-file").addEventListener("change", function (e) {
    var file = e.target.files && e.target.files[0];
    if (!file) return;
    var img = new Image(), url = URL.createObjectURL(file);
    img.onload = function () {
      var scale = Math.min(1, 1400 / Math.max(img.width, img.height));
      var code = decode(img, Math.round(img.width * scale), Math.round(img.height * scale));
      URL.revokeObjectURL(url);
      if (code && code.data) lookup(code.data); else say("No QR code found in that photo. Try a sharper, closer picture.");
    };
    img.onerror = function () { say("That file could not be read as an image."); };
    img.src = url; e.target.value = "";
  });

  document.getElementById("scan-manual").addEventListener("submit", function (e) {
    e.preventDefault();
    var v = document.getElementById("scan-code").value.trim();
    if (v) lookup(v);
  });
})();
