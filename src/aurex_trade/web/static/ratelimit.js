/**
 * HTMX Rate Limit Retry Handler
 *
 * Intercepts 429 responses from the server and automatically retries
 * with exponential backoff before surfacing errors to the user.
 * Uses the Retry-After header when available.
 */
(function () {
  "use strict";

  var MAX_RETRIES = 3;
  var retryState = new WeakMap();

  function getRetryAfter(xhr) {
    var header = xhr.getResponseHeader("Retry-After");
    if (header) {
      var seconds = parseInt(header, 10);
      if (!isNaN(seconds) && seconds > 0) {
        return seconds * 1000;
      }
    }
    return null;
  }

  function getBackoffDelay(attempt) {
    // Exponential backoff: 2s, 4s, 8s
    return Math.pow(2, attempt) * 1000;
  }

  function showRetryIndicator(target) {
    if (!target) return null;
    var indicator = document.createElement("div");
    indicator.className = "alert alert-info alert-sm mt-2 aurex-retry-indicator";
    indicator.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">' +
      '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />' +
      "</svg>" +
      "<span>Rate limited — retrying shortly...</span>";
    target.appendChild(indicator);
    return indicator;
  }

  function removeRetryIndicator(indicator) {
    if (indicator && indicator.parentNode) {
      indicator.parentNode.removeChild(indicator);
    }
  }

  /**
   * Determine the HTTP method and URL from an HTMX element's attributes.
   * Returns {method, url} or null if not determinable.
   */
  function getRequestInfo(elt) {
    var methods = ["get", "post", "put", "patch", "delete"];
    for (var i = 0; i < methods.length; i++) {
      var url = elt.getAttribute("hx-" + methods[i]);
      if (url) {
        return { method: methods[i].toUpperCase(), url: url };
      }
    }
    return null;
  }

  document.body.addEventListener("htmx:beforeOnLoad", function (evt) {
    var xhr = evt.detail.xhr;
    if (xhr.status !== 429) return;

    var elt = evt.detail.elt;
    var state = retryState.get(elt) || { attempts: 0, indicator: null };

    if (state.attempts >= MAX_RETRIES) {
      // Exhausted retries — let the error through to be displayed
      removeRetryIndicator(state.indicator);
      retryState.delete(elt);
      return;
    }

    // Prevent the error from being swapped in
    evt.detail.shouldSwap = false;
    evt.detail.isError = false;

    state.attempts++;
    var retryAfterMs = getRetryAfter(xhr) || getBackoffDelay(state.attempts);

    // Show indicator on first retry
    if (!state.indicator) {
      var target = document.querySelector(
        elt.getAttribute("hx-target") || ""
      ) || elt;
      state.indicator = showRetryIndicator(target);
    }

    retryState.set(elt, state);

    // Schedule retry using htmx.ajax() for a clean request cycle
    var info = getRequestInfo(elt);
    if (info) {
      setTimeout(function () {
        htmx.ajax(info.method, info.url, {
          source: elt,
          target: elt.getAttribute("hx-target") || undefined,
          swap: elt.getAttribute("hx-swap") || undefined
        });
      }, retryAfterMs);
    }
  });

  // Clean up retry state on successful responses
  document.body.addEventListener("htmx:afterOnLoad", function (evt) {
    var xhr = evt.detail.xhr;
    if (xhr.status >= 200 && xhr.status < 300) {
      var elt = evt.detail.elt;
      var state = retryState.get(elt);
      if (state) {
        removeRetryIndicator(state.indicator);
        retryState.delete(elt);
      }
    }
  });
})();
