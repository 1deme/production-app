// საერთო ქცევა ყველა გვერდზე
(function () {
  "use strict";

  // შეტყობინებების (toast) ჩვენება
  function showToasts(root) {
    (root || document).querySelectorAll(".toast:not(.shown)").forEach(function (el) {
      el.classList.add("shown");
      bootstrap.Toast.getOrCreateInstance(el).show();
    });
  }
  document.addEventListener("DOMContentLoaded", function () { showToasts(); });

  // დადასტურების ფანჯარა: <form data-confirm="ტექსტი"> ან <button data-confirm="...">
  var pendingForm = null, pendingSubmitter = null;
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var text = (e.submitter && e.submitter.dataset.confirm) || form.dataset.confirm;
    if (!text || form.dataset.confirmed === "1") return;
    e.preventDefault();
    pendingForm = form;
    pendingSubmitter = e.submitter || null;
    document.getElementById("confirmModalText").textContent = text;
    var okBtn = document.getElementById("confirmModalOk");
    okBtn.className = "btn " + (form.dataset.confirmStyle || "btn-danger");
    var modal = bootstrap.Modal.getOrCreateInstance(document.getElementById("confirmModal"));
    modal.show();
  }, true);
  document.addEventListener("click", function (e) {
    if (e.target.id !== "confirmModalOk" || !pendingForm) return;
    pendingForm.dataset.confirmed = "1";
    bootstrap.Modal.getInstance(document.getElementById("confirmModal")).hide();
    if (pendingForm.requestSubmit) pendingForm.requestSubmit(pendingSubmitter || undefined);
    else pendingForm.submit();
  });
  document.getElementById("confirmModal") && document.getElementById("confirmModal")
    .addEventListener("shown.bs.modal", function () { document.getElementById("confirmModalOk").focus(); });

  // „/“ — ძიების ველზე გადასვლა (თუ ამჟამად სხვა ველში არ ვწერთ)
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    var s = document.querySelector("[data-search-input]");
    if (s) { e.preventDefault(); s.focus(); s.select(); }
  });

  // ცხრილის სტრიქონზე დაჭერით გახსნა: <tr data-href="...">
  document.addEventListener("click", function (e) {
    var row = e.target.closest("tr[data-href]");
    if (!row || e.target.closest("a, button, input, form")) return;
    window.location = row.dataset.href;
  });

  // ორმაგი დაჭერის თავიდან აცილება — ფორმის გაგზავნისას ღილაკები იბლოკება
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || form.hasAttribute("hx-get") || form.method.toLowerCase() !== "post") return;
    setTimeout(function () {
      form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
    }, 0);
  });

  window.App = { showToasts: showToasts };
})();
