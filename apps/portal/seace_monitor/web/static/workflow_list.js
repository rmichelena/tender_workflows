(function (global) {
  "use strict";

  function restoreScrollFromUrl() {
    var params = new URLSearchParams(global.location.search);
    var scroll = params.get("scroll");
    if (!scroll) return;

    var y = parseInt(scroll, 10);
    if (Number.isFinite(y) && y >= 0) {
      function restore() {
        global.scrollTo(0, y);
      }
      restore();
      requestAnimationFrame(restore);
      global.addEventListener("load", restore);
    }

    params.delete("scroll");
    var query = params.toString();
    var next =
      global.location.pathname +
      (query ? "?" + query : "") +
      global.location.hash;
    global.history.replaceState(null, "", next);
  }

  function bindKeepScroll(selector) {
    document.querySelectorAll(selector).forEach(function (form) {
      form.addEventListener("submit", function () {
        var field = form.querySelector('input[name="scroll"]');
        if (field) field.value = String(Math.round(global.scrollY));
      });
    });
  }

  function lockRow(row, cfg) {
    if (!row || !cfg.lockAttr) return;
    row.dataset[cfg.lockAttr] = "1";
    row.querySelectorAll(".js-action-row button").forEach(function (btn) {
      btn.disabled = true;
    });
    if (cfg.lockBtnSelector) {
      var btn = row.querySelector(cfg.lockBtnSelector);
      if (btn && cfg.lockLabel) btn.textContent = cfg.lockLabel;
    }
  }

  function bindLockForms(cfg) {
    if (!cfg.lockFormSelector) return;
    document.querySelectorAll(cfg.lockFormSelector).forEach(function (form) {
      form.addEventListener("submit", function () {
        var field = form.querySelector('input[name="scroll"]');
        if (field) field.value = String(Math.round(global.scrollY));
        lockRow(form.closest("tr"), cfg);
      });
    });
  }

  function startPolling(cfg) {
    if (!cfg.pendingAttr) return;

    var attr = "data-" + cfg.pendingAttr;
    var rows = document.querySelectorAll("tr[" + attr + '="1"]');
    if (!rows.length) return;

    var ids = Array.from(rows)
      .map(function (row) {
        return row.getAttribute("data-process-id");
      })
      .filter(Boolean);

    var stillInList = cfg.stillInList || [];

    function isPending(status) {
      if (typeof stillInList === "function") return stillInList(status);
      return stillInList.indexOf(status) >= 0;
    }

    function poll() {
      Promise.all(
        ids.map(function (id) {
          return fetch("/api/processes/" + id + "/workflow").then(function (r) {
            return r.json();
          });
        })
      )
        .then(function (payloads) {
          var pending = payloads.some(function (row) {
            return isPending(row.status);
          });
          if (!pending) {
            global.location.reload();
          }
        })
        .catch(function () {});
    }

    global.setInterval(poll, cfg.pollIntervalMs || 3000);
  }

  function init(cfg) {
    restoreScrollFromUrl();
    if (cfg.keepScrollForms) {
      bindKeepScroll(cfg.keepScrollForms);
    }
    bindLockForms(cfg);
    startPolling(cfg);
  }

  global.WorkflowList = { init: init };
})(window);
