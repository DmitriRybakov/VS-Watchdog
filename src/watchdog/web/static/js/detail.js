/* Remembering which expanders a colleague left open, per browser.
 *
 * "More details" and "Technical details" are one expander each rather than a
 * checkbox per field, on purpose: a switch per field is a configuration surface
 * nobody maintains, whose failure mode is somebody hiding something and
 * forgetting they did.
 *
 * The preference is a cookie, the same way the reviewer's name is, so it
 * survives a reload and follows the person rather than the notice. It is a
 * display preference and nothing else, so it is written from the browser and
 * never sent anywhere.
 *
 * No framework, no build step, no external request. */
(function () {
  "use strict";

  var PREFIX = "watchdog_open_";
  var YEAR = 365 * 24 * 60 * 60;

  function remembered(name) {
    var pattern = new RegExp("(?:^|; )" + PREFIX + name + "=([^;]*)");
    var found = document.cookie.match(pattern);
    return found ? found[1] : null;
  }

  function remember(name, open) {
    document.cookie =
      PREFIX + name + "=" + (open ? "1" : "0") + ";path=/;max-age=" + YEAR + ";samesite=lax";
  }

  function wire(element) {
    var name = element.getAttribute("data-remember");
    if (!name) return;

    var saved = remembered(name);
    if (saved === "1") element.setAttribute("open", "open");
    else if (saved === "0") element.removeAttribute("open");

    element.addEventListener("toggle", function () {
      remember(name, element.open);
    });
  }

  function wireAll() {
    var all = document.querySelectorAll("details[data-remember]");
    for (var index = 0; index < all.length; index += 1) {
      wire(all[index]);
    }

    var checks = document.querySelectorAll("input[data-remember-check]");
    for (var check = 0; check < checks.length; check += 1) {
      wireCheck(checks[check]);
    }
  }

  /* "Show explanations": one switch for the whole page, remembered the same way.
   * On, the hover marks become lines of text under the values they explain, so
   * the notes can be read, tabbed through or printed without a mouse. */
  function wireCheck(element) {
    var name = element.getAttribute("data-remember-check");
    if (!name || element.getAttribute("data-wired") === "1") return;
    element.setAttribute("data-wired", "1");

    function apply(on) {
      element.checked = on;
      document.body.classList.toggle(name + "-on", on);
    }

    apply(remembered(name) === "1");

    element.addEventListener("change", function () {
      remember(name, element.checked);
      apply(element.checked);
    });
  }

  wireAll();
  document.body.addEventListener("htmx:afterSwap", wireAll);
})();
