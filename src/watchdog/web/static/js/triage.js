/* Keyboard triage.
 *
 * Reviewing two hundred rows with a mouse is what makes people abandon a tool
 * like this, so j, k, Enter, y, n, u and / all work from the list.
 *
 * Moving the cursor costs no request. Every card on the page is already in the
 * document, escaped, with all but one hidden - so j and k swap which one is
 * shown instead of asking the server for it. Recording a verdict does go to the
 * server, but through the button HTMX is already bound to rather than through a
 * hand-rolled fetch, so there is exactly one code path that writes a review.
 *
 * No framework, no build step, no external request. */
(function () {
  "use strict";

  var TYPING = { INPUT: true, SELECT: true, TEXTAREA: true };

  function inAField(element) {
    return (
      !!element &&
      (TYPING[element.tagName] === true || element.isContentEditable === true)
    );
  }

  function results() {
    return document.getElementById("results");
  }

  function rows() {
    var region = results();
    if (!region) return [];
    return Array.prototype.slice.call(
      region.querySelectorAll(".queue-row, .table-row")
    );
  }

  function cards() {
    var region = results();
    if (!region) return [];
    return Array.prototype.slice.call(region.querySelectorAll(".decision-card"));
  }

  function currentIndex() {
    var found = rows().findIndex(function (row) {
      return row.classList.contains("is-current");
    });
    return found < 0 ? 0 : found;
  }

  function show(index) {
    var all = rows();
    if (!all.length) return;
    var target = Math.max(0, Math.min(index, all.length - 1));

    all.forEach(function (row, position) {
      var current = position === target;
      row.classList.toggle("is-current", current);
      if (row.hasAttribute("aria-selected")) {
        row.setAttribute("aria-selected", current ? "true" : "false");
      }
    });

    cards().forEach(function (card, position) {
      var current = position === target;
      card.classList.toggle("is-hidden", !current);
      if (current) {
        card.removeAttribute("hidden");
      } else {
        card.setAttribute("hidden", "hidden");
      }
    });

    var chosen = all[target];
    if (chosen && chosen.scrollIntoView) {
      chosen.scrollIntoView({ block: "nearest" });
    }
  }

  function openDetail() {
    var row = rows()[currentIndex()];
    if (!row) return;
    var href =
      row.getAttribute("data-detail") ||
      (function () {
        var card = cards()[currentIndex()];
        var link = card && card.querySelector(".card-links a");
        return link && link.getAttribute("href");
      })();
    if (href) window.location.assign(href);
  }

  /* Marking goes through the card's own button, so the request, the target and
   * the swap are the ones the server rendered - there is no second definition of
   * what recording a verdict means. */
  function mark(verdict) {
    var card = cards()[currentIndex()];
    if (!card) return;
    var button = card.querySelector('[data-verdict="' + verdict + '"]');
    if (button) button.click();
  }

  function focusSearch() {
    var box = document.getElementById("filter-text");
    if (!box) return;
    box.focus();
    box.select();
  }

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (inAField(event.target) && event.key !== "Escape") return;

    switch (event.key) {
      case "j":
      case "ArrowDown":
        event.preventDefault();
        show(currentIndex() + 1);
        break;
      case "k":
      case "ArrowUp":
        event.preventDefault();
        show(currentIndex() - 1);
        break;
      case "Enter":
        event.preventDefault();
        openDetail();
        break;
      case "y":
        event.preventDefault();
        mark("relevant");
        break;
      case "n":
        event.preventDefault();
        mark("not_relevant");
        break;
      case "u":
        event.preventDefault();
        mark("unsure");
        break;
      case "/":
        event.preventDefault();
        focusSearch();
        break;
      case "Escape":
        if (event.target && event.target.blur) event.target.blur();
        break;
      default:
        break;
    }
  });

  /* Clicking a row selects it, so the pointer and the keyboard agree about which
   * notice the card is showing. */
  document.addEventListener("click", function (event) {
    var row = event.target.closest && event.target.closest(".queue-row, .table-row");
    if (!row) return;
    if (event.target.closest("a, button")) return;
    var index = parseInt(row.getAttribute("data-index"), 10);
    if (!isNaN(index)) show(index);
  });

  /* "Any band" and the individual bands are opposites; checking one clears the
   * other so the form cannot ask for two different things at once. */
  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!input || input.name !== "band") return;
    var boxes = Array.prototype.slice.call(
      document.querySelectorAll('#filters input[name="band"]')
    );
    if (input.value === "all") {
      if (input.checked) {
        boxes.forEach(function (box) {
          if (box.value !== "all") box.checked = false;
        });
      }
    } else if (input.checked) {
      boxes.forEach(function (box) {
        if (box.value === "all") box.checked = false;
      });
    }
  });

  /* After a swap, put the cursor where the server said it is, and refresh the
   * results once when a run has just stopped. */
  document.body &&
    document.body.addEventListener("htmx:afterSwap", function (event) {
      var region = event.detail && event.detail.target;
      if (!region) return;

      if (region.id === "results") {
        var queue = region.querySelector(".triage");
        show(queue ? parseInt(queue.getAttribute("data-cursor"), 10) || 0 : 0);
      }

      /* A name was just saved, so the verdict buttons on every card are still
       * rendered disabled. Re-render the results once so they come back live. */
      if (region.id === "reviewer") {
        if (region.querySelector(".reviewer-set") && window.htmx) {
          window.htmx.ajax("GET", window.location.href, { target: "#results" });
        }
      }

      if (region.id === "run-status") {
        var belt = region.querySelector("[data-run-finished]");
        if (belt && !belt.dataset.refreshed) {
          belt.dataset.refreshed = "1";
          var results = document.getElementById("results");
          if (results && window.htmx) {
            window.htmx.ajax("GET", window.location.href, { target: "#results" });
          }
        }
      }
    });
})();
