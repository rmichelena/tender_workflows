(function () {
  "use strict";

  var root = document.getElementById("analysis-chat");
  if (!root) return;

  var processId = root.getAttribute("data-process-id");
  var messagesEl = document.getElementById("chat-messages");
  var formEl = document.getElementById("chat-form");
  var inputEl = document.getElementById("chat-input");
  var submitEl = document.getElementById("chat-submit");
  var statusEl = document.getElementById("chat-status");
  var panelEl = document.getElementById("chat-panel");
  var unavailableEl = document.getElementById("chat-unavailable");
  var sending = false;

  function setStatus(text) {
    if (!statusEl) return;
    if (!text) {
      statusEl.hidden = true;
      statusEl.textContent = "";
      return;
    }
    statusEl.hidden = false;
    statusEl.textContent = text;
  }

  function setSending(value) {
    sending = value;
    if (submitEl) submitEl.disabled = value;
    if (inputEl) inputEl.disabled = value;
    setStatus(value ? "Consultando Gemini…" : "");
  }

  function scrollToBottom() {
    if (!messagesEl) return;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendBubble(role, text, html) {
    if (!messagesEl) return;
    var wrap = document.createElement("div");
    wrap.className = "chat-bubble chat-bubble-" + role;

    var label = document.createElement("div");
    label.className = "chat-bubble-label";
    label.textContent = role === "user" ? "Tú" : "Gemini";

    var body = document.createElement("div");
    body.className = "chat-bubble-body" + (role === "model" ? " markdown-body" : "");
    if (role === "model" && html) {
      body.innerHTML = html;
    } else {
      body.textContent = text;
    }

    wrap.appendChild(label);
    wrap.appendChild(body);
    messagesEl.appendChild(wrap);
    scrollToBottom();
  }

  function showUnavailable(message) {
    if (panelEl) panelEl.hidden = true;
    if (unavailableEl) {
      unavailableEl.hidden = false;
      if (message) unavailableEl.textContent = message;
    }
  }

  function showPanel() {
    if (unavailableEl) unavailableEl.hidden = true;
    if (panelEl) panelEl.hidden = false;
  }

  function handleError(err) {
    var msg = (err && err.message) || "No se pudo enviar la pregunta.";
    setStatus(msg);
    setSending(false);
  }

  if (formEl) {
    formEl.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (sending || !inputEl) return;
      var message = inputEl.value.trim();
      if (!message) return;

      appendBubble("user", message, null);
      inputEl.value = "";
      setSending(true);

      fetch("/api/analizados/" + processId + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) {
              var detail = data && (data.detail || data.message);
              throw new Error(typeof detail === "string" ? detail : "Error del servidor");
            }
            return data;
          });
        })
        .then(function (data) {
          if (!data.available) {
            showUnavailable(data.message);
            return;
          }
          showPanel();
          appendBubble("model", data.reply, data.reply_html);
        })
        .catch(handleError)
        .finally(function () {
          setSending(false);
        });
    });
  }

  scrollToBottom();
})();
