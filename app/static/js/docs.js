// საბუთების ფორმები: ძიება/სკანირება სტრიქონის დასამატებლად, ჯამები, კლავიატურით ნავიგაცია.
(function () {
  "use strict";

  // ---------------------------------------------------------------- რიცხვები
  function num(s) {
    s = String(s == null ? "" : s).replace(/[\s  ₾']/g, "");
    if (!s) return null;
    if (s.indexOf(",") >= 0 && s.indexOf(".") >= 0) {
      if (s.lastIndexOf(",") > s.lastIndexOf(".")) s = s.replace(/\./g, "").replace(",", ".");
      else s = s.replace(/,/g, "");
    } else {
      s = s.replace(",", ".");
    }
    var v = Number(s);
    return isFinite(v) ? v : null;
  }
  function fmt(v, places, group) {
    if (v == null || !isFinite(v)) return "";
    var s = (Math.round(v * Math.pow(10, places)) / Math.pow(10, places)).toFixed(places);
    var parts = s.split(".");
    if (group !== false) parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return parts[0] + (parts[1] ? "," + parts[1] : "");
  }
  function trim(s) { return s.indexOf(",") >= 0 ? s.replace(/0+$/, "").replace(/,$/, "") : s; }
  window.Num = { parse: num, fmt: fmt };

  // ---------------------------------------------------------------- ძიება / სკანირება (picker)
  function initPicker(root) {
    var input = root.querySelector(".picker-input");
    var menu = root.querySelector(".picker-menu");
    var target = document.querySelector(root.dataset.target);
    var items = [], active = -1, timer = null, seq = 0, shownFor = null;

    function hide() { menu.hidden = true; active = -1; shownFor = null; }
    function lookup(q) {
      var my = ++seq;
      return fetch(root.dataset.lookup + "?q=" + encodeURIComponent(q), { headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (d) { return my === seq ? d : null; });
    }
    function highlight() {
      menu.querySelectorAll(".picker-item").forEach(function (el, i) { el.classList.toggle("active", i === active); });
    }
    function render(d, q) {
      items = d.items || [];
      active = items.length ? 0 : -1;
      shownFor = q;
      menu.innerHTML = "";
      items.forEach(function (it, i) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "list-group-item list-group-item-action picker-item d-flex justify-content-between gap-2";
        var left = document.createElement("span");
        left.innerHTML = "<span class='fw-semibold'></span><br><small class='text-muted'></small>";
        left.children[0].textContent = it.name;
        left.children[2].textContent = it.sub || "";
        var right = document.createElement("small");
        right.className = "text-muted text-nowrap";
        right.textContent = it.stock || "";
        b.appendChild(left); b.appendChild(right);
        b.addEventListener("mousedown", function (e) { e.preventDefault(); pick(it.id); });
        menu.appendChild(b);
      });
      if (!items.length) {
        var empty = document.createElement("div");
        empty.className = "list-group-item text-muted";
        empty.textContent = root.dataset.notFound.replace("{q}", q);
        menu.appendChild(empty);
        if (root.dataset.create) {
          var c = document.createElement("button");
          c.type = "button";
          c.className = "list-group-item list-group-item-action picker-item text-primary fw-semibold";
          c.innerHTML = "<i class='bi bi-plus-lg me-1'></i>";
          c.appendChild(document.createTextNode(root.dataset.createLabel));
          c.addEventListener("mousedown", function (e) { e.preventDefault(); openCreate(q); });
          menu.appendChild(c);
          active = 0;
          items = [{ create: true }];
        }
      }
      menu.hidden = false;
      highlight();
    }
    function pick(id) {
      hide();
      input.value = "";
      var existing = target.querySelector('[data-item-id="' + id + '"]');
      if (existing) { focusRow(existing, true); return; }
      fetch(root.dataset.rowUrl + (root.dataset.rowUrl.indexOf("?") >= 0 ? "&" : "?") + "id=" + id)
        .then(function (r) { return r.text(); })
        .then(function (html) {
          target.insertAdjacentHTML("beforeend", html);
          var row = target.lastElementChild;
          var empty = document.querySelector("[data-empty-lines]");
          if (empty) empty.hidden = true;
          focusRow(row, false);
          document.dispatchEvent(new CustomEvent("doc:changed"));
        });
    }
    function openCreate(q) {
      hide();
      if (window.QuickCreate) window.QuickCreate.open(q, function (newId) { pick(newId); });
    }
    root.pick = pick;

    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (!q) { hide(); return; }
      timer = setTimeout(function () { lookup(q).then(function (d) { if (d) render(d, q); }); }, 200);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (menu.hidden || !items.length) return;
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        highlight();
      } else if (e.key === "Enter") {
        e.preventDefault();
        clearTimeout(timer);
        var q = input.value.trim();
        if (!q) return;
        if (!menu.hidden && shownFor === q && active >= 0 && items[active]) {
          if (items[active].create) openCreate(q); else pick(items[active].id);
          return;
        }
        // სკანერი: ახალი ძიება დაუყოვნებლივ; ზუსტი შტრიხკოდი ან ერთადერთი შედეგი → პირდაპირ ემატება
        lookup(q).then(function (d) {
          if (!d) return;
          if (d.exact_id) pick(d.exact_id);
          else if (d.items.length === 1) pick(d.items[0].id);
          else render(d, q);
        });
      } else if (e.key === "Escape") {
        input.value = ""; hide();
      }
    });
    input.addEventListener("blur", function () { setTimeout(hide, 150); });
  }

  function focusRow(row, flash) {
    var f = row.querySelector("[data-focus]") || row.querySelector("input:not([type=hidden])");
    if (f) { f.focus(); if (f.select) f.select(); }
    if (flash) {
      row.classList.remove("row-flash");
      void row.offsetWidth;
      row.classList.add("row-flash");
    }
  }

  // ---------------------------------------------------------------- ჯამები
  function recalcRow(row, edited) {
    var qty = num((row.querySelector(".js-qty") || {}).value);
    var price = row.querySelector(".js-price");
    var amount = row.querySelector(".js-amount");
    var source = row.querySelector(".js-source");
    var factorSel = row.querySelector(".js-unit");
    if (amount && amount.tagName === "INPUT") {
      if (edited && edited.classList.contains("js-amount")) {
        if (source) source.value = "amount";
        var a = num(amount.value);
        if (price && qty && a != null) price.value = trim(fmt(a / qty, 4, false));
      } else if (price) {
        if (source && edited && edited.classList.contains("js-price")) source.value = "price";
        var p = num(price.value);
        if (source && source.value === "amount") {
          var a2 = num(amount.value);
          if (qty && a2 != null) price.value = trim(fmt(a2 / qty, 4, false));
        } else if (p != null && qty != null) {
          amount.value = fmt(qty * p, 2, false);
        }
      }
    } else if (amount) {  // მხოლოდ ჩვენება (გატანა)
      var pp = price ? num(price.value) : null;
      amount.textContent = (pp != null && qty != null) ? fmt(qty * pp, 2) + " ₾" : "—";
      amount.dataset.value = (pp != null && qty != null) ? qty * pp : 0;
    }
    // ხელმისაწვდომი ნაშთის გაფრთხილება
    var avail = row.dataset.available != null ? num(row.dataset.available) : null;
    var warn = row.querySelector(".js-avail-warn");
    if (warn && avail != null) warn.hidden = !(qty != null && qty > avail + 1e-9);
    // რეცეპტის ღირებულება
    var cost = row.dataset.cost;
    var lineCost = row.querySelector("[data-line-cost]");
    if (lineCost) {
      var factor = factorSel ? num(factorSel.selectedOptions[0].dataset.factor) : 1;
      var waste = num((document.getElementById("waste_percent") || {}).value) || 0;
      if (cost === "" || cost == null || qty == null) {
        lineCost.textContent = cost === "" ? lineCost.dataset.noPrice : "—";
        lineCost.dataset.value = 0;
      } else {
        var v = qty * factor * Number(cost) * (1 + waste / 100);
        lineCost.dataset.value = v;
        lineCost.textContent = fmt(v, 2) + " ₾";
      }
    }
  }

  function recalcTotals(form) {
    var total = 0;
    form.querySelectorAll("[data-line]").forEach(function (row) {
      var a = row.querySelector(".js-amount");
      if (a) total += (a.tagName === "INPUT" ? num(a.value) : Number(a.dataset.value)) || 0;
      var lc = row.querySelector("[data-line-cost]");
      if (lc) total += Number(lc.dataset.value) || 0;
    });
    form.querySelectorAll("[data-total]").forEach(function (el) { el.textContent = fmt(total, 2) + " ₾"; });
    var y = num((document.getElementById("yield_qty") || {}).value);
    form.querySelectorAll("[data-unit-cost]").forEach(function (el) {
      el.textContent = y ? fmt(total / y, 2) + " ₾" : "—";
    });
    var count = form.querySelectorAll("[data-line]").length;
    form.querySelectorAll("[data-line-count]").forEach(function (el) { el.textContent = count; });
    var empty = form.querySelector("[data-empty-lines]");
    if (empty) empty.hidden = count > 0;
  }

  function initDocForm(form) {
    var dirty = false;
    form.addEventListener("input", function (e) {
      dirty = true;
      var row = e.target.closest("[data-line]");
      if (row) recalcRow(row, e.target);
      if (e.target.id === "waste_percent") form.querySelectorAll("[data-line]").forEach(function (r) { recalcRow(r); });
      recalcTotals(form);
    });
    form.addEventListener("change", function (e) {
      var row = e.target.closest("[data-line]");
      if (row) { recalcRow(row); recalcTotals(form); }
    });
    form.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-remove-row");
      if (!btn) return;
      btn.closest("[data-line]").remove();
      dirty = true;
      recalcTotals(form);
    });
    // Enter: ფორმა არ იგზავნება — გადადის შემდეგ ველზე, სტრიქონის ბოლოს კი ისევ ძიებაზე (შემდეგი სკანირება)
    form.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.target.tagName === "TEXTAREA" || e.target.tagName === "BUTTON") return;
      if (e.target.classList.contains("picker-input")) return;
      e.preventDefault();
      var row = e.target.closest("[data-line]");
      var scope = row || form;
      var fields = Array.prototype.filter.call(
        scope.querySelectorAll("input:not([type=hidden]):not([readonly]), select"),
        function (el) { return !el.disabled && el.offsetParent !== null && !el.classList.contains("picker-input"); });
      var idx = fields.indexOf(e.target);
      if (idx >= 0 && idx < fields.length - 1) { fields[idx + 1].focus(); if (fields[idx + 1].select) fields[idx + 1].select(); return; }
      var picker = form.querySelector(".picker-input");
      if (picker) picker.focus();
    });
    document.addEventListener("doc:changed", function () {
      dirty = true;
      form.querySelectorAll("[data-line]").forEach(function (r) { recalcRow(r); });
      recalcTotals(form);
    });
    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) {
      if (dirty) { e.preventDefault(); e.returnValue = ""; }
    });
    form.querySelectorAll("[data-line]").forEach(function (r) { recalcRow(r); });
    recalcTotals(form);
  }

  // ---------------------------------------------------------------- ახალი ინგრედიენტის სწრაფი შექმნა
  function initQuickCreate() {
    var modalEl = document.getElementById("quickCreateModal");
    if (!modalEl) return;
    var form = modalEl.querySelector("form");
    var callback = null;
    window.QuickCreate = {
      open: function (q, cb) {
        callback = cb;
        form.reset();
        form.querySelectorAll(".invalid-feedback").forEach(function (el) { el.textContent = ""; });
        form.querySelectorAll(".is-invalid").forEach(function (el) { el.classList.remove("is-invalid"); });
        var digits = (q.match(/\d/g) || []).length;
        var isBarcode = q.indexOf(" ") < 0 && q.length >= 4 && digits / q.length >= 0.6;
        form.elements.name.value = isBarcode ? "" : q;
        form.elements.barcode.value = isBarcode ? q : "";
        var m = bootstrap.Modal.getOrCreateInstance(modalEl);
        m.show();
        modalEl.addEventListener("shown.bs.modal", function once() {
          modalEl.removeEventListener("shown.bs.modal", once);
          form.elements.name.focus();
        });
      }
    };
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      e.stopPropagation();
      fetch(form.action, { method: "POST", body: new FormData(form) })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            bootstrap.Modal.getInstance(modalEl).hide();
            if (callback) callback(d.id);
            return;
          }
          Object.keys(d.errors || {}).forEach(function (field) {
            var input = form.elements[field === "barcodes" ? "barcode" : field];
            var fb = form.querySelector('[data-error-for="' + field + '"]');
            if (input) input.classList.add("is-invalid");
            if (fb) fb.textContent = d.errors[field].join(" ");
          });
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-picker]").forEach(initPicker);
    document.querySelectorAll("[data-doc-form]").forEach(initDocForm);
    initQuickCreate();
  });
})();
