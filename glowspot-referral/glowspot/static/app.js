(function () {
  // Confirm before destructive actions.
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Live check of the referrer's phone number while registering a customer.
  var ref = document.getElementById("referrer_phone");
  if (ref) {
    var out = document.getElementById("ref-result"), phone = document.getElementById("phone"), timer;
    var check = function () {
      var v = ref.value.trim();
      if (!v) { out.textContent = ""; out.className = "hint"; return; }
      var url = "/api/referrer?phone=" + encodeURIComponent(v) + "&own=" + encodeURIComponent(phone ? phone.value : "");
      fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (j) {
        out.textContent = j.found ? "Referrer found: " + j.name : j.message;
        out.className = j.found ? "hint good" : "hint bad";
      }).catch(function () { out.textContent = ""; });
    };
    ref.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(check, 350); });
    if (ref.value) check();
  }

  // Sale form: show the right fields and the amount due.
  var form = document.getElementById("sale-form");
  if (form) {
    var due = document.getElementById("due"), pkg = document.getElementById("package"), disc = document.getElementById("discount");
    var fmt = function (n) { return "TZS " + Math.max(0, n).toLocaleString("en-US"); };
    var update = function () {
      var kind = form.querySelector("input[name=kind]:checked").value;
      form.querySelectorAll(".skin-only").forEach(function (el) { el.hidden = kind !== "skin"; });
      form.querySelectorAll(".pkg-only").forEach(function (el) { el.hidden = kind !== "package"; });
      if (kind === "skin") { due.textContent = fmt(parseInt(due.dataset.skin, 10)); return; }
      var opt = pkg.options[pkg.selectedIndex];
      var price = opt && opt.dataset.price ? parseInt(opt.dataset.price, 10) : 0;
      var d = parseInt((disc.value || "0").replace(/[^0-9]/g, ""), 10) || 0;
      due.textContent = price ? fmt(price - d) : "Choose a package";
    };
    form.addEventListener("change", update);
    form.addEventListener("input", update);
    update();
  }
})();
