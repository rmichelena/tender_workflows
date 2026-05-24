(function () {
  "use strict";

  var entities = [];
  var baseline = new Set();
  var selected = new Set();
  var dirty = false;
  var preview = null;
  var saving = false;

  var listEl = document.getElementById("entity-list");
  var loadingEl = document.getElementById("entity-loading");
  var searchEl = document.getElementById("entity-search");
  var filterSelectedEl = document.getElementById("filter-selected-only");
  var filterActivoEl = document.getElementById("filter-activo-only");
  var summaryEl = document.getElementById("selection-summary");
  var summaryFooterEl = document.getElementById("selection-summary-footer");
  var modal = document.getElementById("save-modal");
  var modalAdded = document.getElementById("modal-added-section");
  var modalRemoved = document.getElementById("modal-removed-section");
  var modalRemovedText = document.getElementById("modal-removed-text");
  var removedPolicyOptions = document.getElementById("removed-policy-options");
  var sinceDateInput = document.getElementById("since-date-input");
  var sinceDateError = document.getElementById("since-date-error");

  if (modal && modal.parentElement !== document.body) {
    document.body.appendChild(modal);
  }

  function normalizeRuc(ruc) {
    return String(ruc || "").replace(/\D/g, "");
  }

  function setDirty(value) {
    dirty = value;
    document.querySelectorAll(".js-btn-save").forEach(function (btn) {
      btn.disabled = !dirty || saving;
    });
    document.querySelectorAll(".js-btn-discard").forEach(function (btn) {
      btn.disabled = !dirty || saving;
    });
  }

  function setSaving(value) {
    saving = value;
    setDirty(dirty);
    document.querySelectorAll(".js-btn-save").forEach(function (btn) {
      btn.textContent = saving ? "Guardando…" : "Guardar cambios";
    });
  }

  function updateSummary() {
    var text = selected.size + " seleccionadas de " + entities.length + " entidades";
    if (summaryEl) summaryEl.textContent = text;
    if (summaryFooterEl) summaryFooterEl.textContent = text;
  }

  function matchesFilter(ent) {
    var q = (searchEl.value || "").trim().toLowerCase();
    if (filterSelectedEl.checked && !selected.has(ent.ruc)) return false;
    if (filterActivoEl.checked && (ent.estado_osce || "").toLowerCase() !== "activo") {
      return false;
    }
    if (!q) return true;
    var hay = (
      ent.nombre + " " + ent.ruc + " " + ent.departamento + " " + ent.provincia
    ).toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function entityLineHtml(ent) {
    var parts = [
      '<span class="entity-row-line">',
      escapeHtml(ent.nombre),
      '<span class="entity-row-sep">·</span>',
      escapeHtml(ent.ruc),
    ];
    if (ent.estado_osce) {
      parts.push('<span class="entity-row-sep">·</span>');
      parts.push('<span class="entity-row-extra">' + escapeHtml(ent.estado_osce) + "</span>");
    }
    if (ent.departamento) {
      parts.push('<span class="entity-row-sep">·</span>');
      parts.push('<span class="entity-row-extra">' + escapeHtml(ent.departamento) + "</span>");
    }
    parts.push("</span>");
    return parts.join("");
  }

  function renderList() {
    listEl.innerHTML = "";
    var visible = 0;
    entities.forEach(function (ent) {
      if (!matchesFilter(ent)) return;
      visible += 1;
      var li = document.createElement("li");
      li.className = "entity-row";
      var label = document.createElement("label");
      label.className = "entity-row-label";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selected.has(ent.ruc);
      cb.dataset.ruc = ent.ruc;
      cb.addEventListener("change", function () {
        if (cb.checked) selected.add(ent.ruc);
        else selected.delete(ent.ruc);
        setDirty(!setsEqual(selected, baseline));
        updateSummary();
      });
      var line = document.createElement("span");
      line.innerHTML = entityLineHtml(ent);
      label.appendChild(cb);
      label.appendChild(line);
      li.appendChild(label);
      listEl.appendChild(li);
    });
    if (visible === 0) {
      var empty = document.createElement("li");
      empty.className = "muted entity-empty";
      empty.textContent = "Sin resultados.";
      listEl.appendChild(empty);
    }
  }

  function setsEqual(a, b) {
    if (a.size !== b.size) return false;
    var ok = true;
    a.forEach(function (v) {
      if (!b.has(v)) ok = false;
    });
    return ok;
  }

  function loadEntities() {
    fetch("/api/settings/entidades")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        entities = data.entities || [];
        selected = new Set();
        baseline = new Set();
        entities.forEach(function (e) {
          if (e.activa) {
            selected.add(e.ruc);
            baseline.add(e.ruc);
          }
        });
        loadingEl.hidden = true;
        listEl.hidden = false;
        setDirty(false);
        updateSummary();
        renderList();
      })
      .catch(function (err) {
        loadingEl.textContent = "Error cargando entidades: " + err.message;
      });
  }

  function discardChanges() {
    selected = new Set(baseline);
    setDirty(false);
    updateSummary();
    renderList();
  }

  function validateSinceDate(text) {
    return /^\d{2}\/\d{2}\/\d{2}$/.test(text.trim());
  }

  function buildRemovedPolicyOptions(counts) {
    removedPolicyOptions.innerHTML = "";
    var opts = [
      { value: "keep_all", label: "Mantener todos en la base de datos" },
    ];
    if (counts.analizados > 0) {
      opts.push({
        value: "keep_analyzed",
        label: "Mantener solo los analizados",
      });
    }
    opts.push({
      value: "discard_all",
      label: "Descartar todos (analizados van a archivados)",
    });
    opts.forEach(function (o, i) {
      var label = document.createElement("label");
      label.className = "modal-option";
      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "removed_policy";
      radio.value = o.value;
      if (i === 0) radio.checked = true;
      label.appendChild(radio);
      label.appendChild(document.createTextNode(" " + o.label));
      removedPolicyOptions.appendChild(label);
    });
  }

  function openModal(data) {
    preview = data;
    modalAdded.hidden = !(data.added && data.added.length);
    var c = data.removed_counts || {};
    var hasRemovedProcesses =
      data.removed_entity_ids &&
      data.removed_entity_ids.length &&
      (c.publicados + c.descargados + c.analizados) > 0;
    modalRemoved.hidden = !hasRemovedProcesses;
    if (!modalAdded.hidden) {
      var yearRadio = modal.querySelector('input[name="scan_mode"][value="current_year"]');
      if (yearRadio) yearRadio.checked = true;
      sinceDateInput.value = data.default_since_date || sinceDateInput.value;
      sinceDateError.hidden = true;
    }
    if (!modalRemoved.hidden) {
      modalRemovedText.textContent =
        "Está eliminando entidades de la lista que tienen procesos en la base de datos: " +
        c.publicados + " publicados, " + c.descargados + " descargados, " +
        c.analizados + " analizados. ¿Qué desea hacer con esos procesos?";
      buildRemovedPolicyOptions(c);
    }
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    preview = null;
  }

  function selectedScanMode() {
    var checked = modal.querySelector('input[name="scan_mode"]:checked');
    return checked ? checked.value : null;
  }

  function selectedRemovedPolicy() {
    var checked = modal.querySelector('input[name="removed_policy"]:checked');
    return checked ? checked.value : "keep_all";
  }

  function parseErrorDetail(errBody) {
    if (!errBody) return "Error";
    if (typeof errBody.detail === "string") return errBody.detail;
    if (Array.isArray(errBody.detail)) {
      return errBody.detail.map(function (d) { return d.msg || String(d); }).join("; ");
    }
    return "Error";
  }

  function confirmSave() {
    var payload = {
      selected_rucs: Array.from(selected),
      removed_policy: "keep_all",
    };
    if (preview && preview.added && preview.added.length) {
      var mode = selectedScanMode();
      if (!mode) {
        alert("Seleccione un criterio de escaneo.");
        return;
      }
      payload.added_scan_mode = mode;
      if (mode === "since_date") {
        if (!validateSinceDate(sinceDateInput.value)) {
          sinceDateError.hidden = false;
          return;
        }
        sinceDateError.hidden = true;
        payload.since_date = sinceDateInput.value.trim();
      }
    }
    if (preview && preview.removed_entity_ids && preview.removed_entity_ids.length) {
      var rc = preview.removed_counts || {};
      if (rc.publicados + rc.descargados + rc.analizados > 0) {
        payload.removed_policy = selectedRemovedPolicy();
      }
    }
    setSaving(true);
    fetch("/api/settings/entidades/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(parseErrorDetail(e));
          });
        }
        return r.json();
      })
      .then(function () {
        baseline = new Set(selected);
        setDirty(false);
        closeModal();
        alert(
          "Cambios guardados." +
            (payload.added_scan_mode ? " Escaneo iniciado en segundo plano." : "")
        );
      })
      .catch(function (err) {
        alert("No se pudo guardar: " + err.message);
      })
      .finally(function () {
        setSaving(false);
      });
  }

  function onSaveClick() {
    setSaving(true);
    fetch("/api/settings/entidades/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_rucs: Array.from(selected) }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        var hasAdded = data.added && data.added.length;
        var c = data.removed_counts || {};
        var hasRemovedProcesses =
          data.removed_entity_ids &&
          data.removed_entity_ids.length &&
          (c.publicados + c.descargados + c.analizados) > 0;
        if (!hasAdded && !hasRemovedProcesses) {
          preview = { added: [], removed_entity_ids: [] };
          confirmSave();
          return;
        }
        setSaving(false);
        openModal(data);
      })
      .catch(function (err) {
        setSaving(false);
        alert("Error al preparar guardado: " + err.message);
      });
  }

  window.addEventListener("beforeunload", function (e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });

  document.querySelectorAll(".sidebar nav a").forEach(function (link) {
    link.addEventListener("click", function (e) {
      if (!dirty) return;
      var ok = window.confirm(
        "Tiene cambios sin guardar. ¿Está seguro que quiere salir de la página?"
      );
      if (!ok) e.preventDefault();
    });
  });

  searchEl.addEventListener("input", renderList);
  filterSelectedEl.addEventListener("change", renderList);
  filterActivoEl.addEventListener("change", renderList);
  document.querySelectorAll(".js-btn-discard").forEach(function (btn) {
    btn.addEventListener("click", discardChanges);
  });
  document.querySelectorAll(".js-btn-save").forEach(function (btn) {
    btn.addEventListener("click", onSaveClick);
  });
  document.getElementById("modal-cancel").addEventListener("click", closeModal);
  document.getElementById("modal-confirm").addEventListener("click", confirmSave);
  modal.addEventListener("click", function (e) {
    if (e.target === modal) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });

  loadEntities();
})();
