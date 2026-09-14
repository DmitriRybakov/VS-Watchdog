/* Saying so when a click did nothing.
 *
 * HTMX does not swap a 5xx response, and it cannot swap a response that never
 * arrived. Without this file an outage looks like a dead page: the filter, the
 * "Update from TED" button and the status poll all appear to do nothing at all,
 * which is worse than an error, because nothing on screen says anything went
 * wrong. The 503 page is real but is only reached by loading a whole page.
 *
 * So one banner appears, at the top of the window rather than beside whichever
 * control failed, since that control may be far down a long register. It says
 * what failed and offers the one recovery that is safe for that request.
 *
 * Three rules this file keeps:
 *
 * - The banner carries no status line, no response body and no server text. The
 *   page says what happened, the log says why - the same split as the 503 page.
 * - **A write is never resent.** The connection dropping after the request went
 *   out is indistinguishable from before, so the write may well have happened;
 *   resending it would risk recording a colleague's decision twice. The offer
 *   for those is to reload and look.
 * - **Nothing else clears a warning about a write.** The status belt polls every
 *   second while a run is going, so a banner that any success took away would
 *   let a failed save announce itself and be wiped before anyone read it. That
 *   warning goes when the colleague dismisses it or leaves the page, and at no
 *   other time. A failed read clears on its own retry as well.
 *
 * No framework, no build step, no external request. */
(function () {
  "use strict";

  var ID = "outage-banner";

  /* One sentence for what failed, one for what it means. They are separate
   * because the second depends on whether the request was reading or writing:
   * "nothing changed" is a lie about a write that may have gone through. */
  var UNREACHABLE =
    "Watchdog could not be reached: the connection dropped, or the service is starting up.";
  var NO_DATABASE = "Watchdog cannot reach the database at the moment.";
  var SERVER_FAULT =
    "Something went wrong inside Watchdog. Whoever looks after it can see why in the log.";

  var READING = " Nothing on this page changed, and nothing already recorded is lost.";
  var WRITING =
    " Your last action may not have been saved. Reload the page and check before repeating it.";

  function cause(status) {
    if (status === 503) return NO_DATABASE;
    if (status >= 500) return SERVER_FAULT;
    return UNREACHABLE;
  }

  function existing() {
    return document.getElementById(ID);
  }

  function dismiss() {
    var banner = existing();
    if (banner) banner.remove();
  }

  function inPage(element) {
    return !!(element && element.nodeType === 1 && document.contains(element));
  }

  /* Whether the failed request can be repeated as it was: through the same
   * element into the same target, so the same filter values, swap style and
   * pushed URL. A write is not, and neither is a control the page has since
   * replaced - those get a button that says it is reloading the page. */
  function replayable(verb, detail) {
    var config = (detail && detail.requestConfig) || {};
    return !!(
      verb === "get" &&
      window.htmx &&
      config.path &&
      inPage(detail.elt) &&
      inPage(detail.target)
    );
  }

  function replay(detail) {
    dismiss();
    try {
      window.htmx.ajax("get", detail.requestConfig.path, {
        source: detail.elt,
        target: detail.target,
      });
    } catch (ignored) {
      window.location.reload();
    }
  }

  function show(status, detail) {
    var config = (detail && detail.requestConfig) || {};
    var verb = String(config.verb || "get").toLowerCase();
    var kind = verb === "get" ? "read" : "write";

    var already = existing();
    if (already && already.getAttribute("data-kind") === "write" && kind === "read") {
      /* A failed poll must not take away an unanswered question about a save. */
      return;
    }
    dismiss();

    var canReplay = replayable(verb, detail);

    var banner = document.createElement("div");
    banner.id = ID;
    banner.className = "outage notice notice-error";
    banner.setAttribute("role", "alert");
    banner.setAttribute("data-kind", kind);

    var text = document.createElement("p");
    text.className = "outage-text";
    text.textContent = cause(status) + (kind === "write" ? WRITING : READING);

    var actions = document.createElement("p");
    actions.className = "outage-actions";

    var again = document.createElement("button");
    again.type = "button";
    again.className = "button";
    again.textContent = canReplay ? "Try again" : "Reload the page";
    again.addEventListener("click", function () {
      if (canReplay) replay(detail);
      else window.location.reload();
    });

    var close = document.createElement("button");
    close.type = "button";
    close.className = "link-button";
    close.textContent = "Dismiss";
    close.addEventListener("click", dismiss);

    actions.appendChild(again);
    actions.appendChild(close);
    banner.appendChild(text);
    banner.appendChild(actions);
    document.body.appendChild(banner);
  }

  /* A 5xx: the response arrived, and HTMX deliberately did not swap it. 4xx is
   * left alone - a session that has ended answers with HX-Redirect and takes the
   * whole browser to sign-in, and a page answers the rest itself. */
  document.body.addEventListener("htmx:responseError", function (event) {
    var status = (event.detail && event.detail.xhr && event.detail.xhr.status) || 0;
    if (status >= 500) show(status, event.detail);
  });

  /* No response at all: the socket failed, or it never answered in time. */
  document.body.addEventListener("htmx:sendError", function (event) {
    show(0, event.detail);
  });
  document.body.addEventListener("htmx:timeout", function (event) {
    show(0, event.detail);
  });
})();
