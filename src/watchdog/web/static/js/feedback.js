/* Feedback mode.
 *
 * While it is on, clicking anything on any page opens a comment box and does
 * NOT run the thing that was clicked. That is the whole design: nothing has to
 * be added to individual templates, so nothing has to be stripped out of them
 * later. Comment icons beside every control would be dozens of template edits
 * to add and the same number to remove.
 *
 * Three rules this file keeps:
 *
 * - The click is stopped in the capture phase, before HTMX or a form sees it,
 *   so "Update from TED", "Re-screen" and "Save" open the box and start nothing.
 * - The toggle, the comment box and the exit control stay usable. Intercepting
 *   those would make the mode impossible to leave.
 * - It captures an identifier and a visible label, and nothing else. Never the
 *   value of an input, a password field or a token - a comments table that
 *   collected those would be a breach waiting to be noticed.
 *
 * No framework, no build step, no external request. */
(function () {
  "use strict";

  /* Anything inside these is the feedback machinery itself and is left alone. */
  var EXEMPT = "#feedback-dialog, #feedback-toggle, .feedback-mode";

  /* Elements whose text is a value somebody typed. Their label comes from the
   * associated <label>, the aria-label or the name - never from the contents. */
  var VALUE_HOLDING = { INPUT: true, TEXTAREA: true, SELECT: true };

  var MAX_LABEL = 200;

  function on() {
    return document.body.getAttribute("data-feedback") === "on";
  }

  function exempt(element) {
    return !!(element && element.closest && element.closest(EXEMPT));
  }

  function identifier(element) {
    if (element.id) return "#" + element.id;

    var explicit = element.getAttribute("data-feedback-id");
    if (explicit) return explicit;

    /* A short path, not a full one: three levels is enough to tell two controls
     * apart and stays stable when something unrelated moves on the page. */
    var parts = [];
    var node = element;
    for (var depth = 0; node && depth < 3; depth += 1) {
      var part = node.tagName ? node.tagName.toLowerCase() : "";
      if (node.id) {
        parts.unshift("#" + node.id);
        break;
      }
      if (node.classList && node.classList.length) {
        part += "." + node.classList[0];
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function label(element) {
    var aria = element.getAttribute("aria-label");
    if (aria) return trim(aria);

    if (VALUE_HOLDING[element.tagName] === true) {
      /* Deliberately not element.value. */
      var own = element.closest("label");
      if (own) return trim(textWithoutFields(own));
      var named = element.getAttribute("name");
      return named ? trim(named) : trim(element.tagName.toLowerCase());
    }

    var title = element.getAttribute("title");
    if (title) return trim(title);

    return trim(textWithoutFields(element)) || trim(element.tagName.toLowerCase());
  }

  /* The visible text of an element with every field's contents removed, so a
   * label can never pick up what somebody typed into a box inside it. */
  function textWithoutFields(element) {
    var copy = element.cloneNode(true);
    var fields = copy.querySelectorAll("input, textarea, select");
    for (var index = 0; index < fields.length; index += 1) {
      fields[index].remove();
    }
    return copy.textContent || "";
  }

  function trim(text) {
    var cleaned = String(text).replace(/\s+/g, " ").trim();
    return cleaned.length > MAX_LABEL ? cleaned.slice(0, MAX_LABEL) : cleaned;
  }

  /* The notice this page is about, when it is about one. Read from the detail
   * page's own marker rather than parsed out of the URL. */
  function noticeId() {
    var marker = document.querySelector("[data-tender-id]");
    return marker ? marker.getAttribute("data-tender-id") : "";
  }

  function open(element) {
    var dialog = document.getElementById("feedback-dialog");
    if (!dialog) return;

    setValue("feedback-page", window.location.pathname);
    setValue("feedback-element", identifier(element));
    setValue("feedback-element-label", label(element));
    setValue("feedback-tender", noticeId());

    var what = document.getElementById("feedback-what-label");
    if (what) {
      what.textContent = label(element) + "  (" + identifier(element) + ")";
    }

    var box = document.getElementById("feedback-comment");
    if (box) box.value = "";

    var result = document.getElementById("feedback-result");
    if (result) result.innerHTML = "";

    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
    if (box) box.focus();
  }

  function setValue(id, value) {
    var field = document.getElementById(id);
    if (field) field.value = value || "";
  }

  document.addEventListener(
    "click",
    function (event) {
      if (!on()) return;

      var element = event.target;
      if (!element || !element.closest) return;
      if (exempt(element)) return;

      /* Capture phase and all three stoppers: HTMX binds on the element itself,
       * and a form submit would otherwise still go through. */
      event.preventDefault();
      event.stopPropagation();
      if (event.stopImmediatePropagation) event.stopImmediatePropagation();

      open(element);
    },
    true
  );

  /* A form in feedback mode never submits, however it was triggered - Enter in a
   * text box does not go through the click handler above. */
  document.addEventListener(
    "submit",
    function (event) {
      if (!on()) return;
      if (exempt(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.stopImmediatePropagation) event.stopImmediatePropagation();
      open(event.target);
    },
    true
  );

  document.addEventListener("click", function (event) {
    if (event.target && event.target.id === "feedback-close") {
      var dialog = document.getElementById("feedback-dialog");
      if (dialog && typeof dialog.close === "function") dialog.close();
      else if (dialog) dialog.removeAttribute("open");
    }
  });

  /* The toggle swaps itself, so the body attribute is updated from what came
   * back rather than from a page reload. */
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (!event.target || event.target.id !== "feedback-toggle") return;
    var form = event.target.querySelector("[data-feedback-state]");
    var state = form ? form.getAttribute("data-feedback-state") : "off";
    document.body.setAttribute("data-feedback", state);
  });
})();
