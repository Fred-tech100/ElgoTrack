(function () {
  var ds = document.getElementById("dataset"), gb = document.getElementById("group_by");
  if (!ds || !gb) return;
  function sync() {
    Array.prototype.forEach.call(gb.options, function (o) {
      if (!o.dataset.dataset) return;
      var show = o.dataset.dataset === ds.value;
      o.hidden = !show; o.disabled = !show;
      if (!show && o.selected) gb.value = "";
    });
  }
  ds.addEventListener("change", sync);
  sync();
})();
