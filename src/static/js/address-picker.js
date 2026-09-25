/* The address picker, for the public forms and the admin.
 *
 * One behaviour, two places to put the answer. On a public form the places
 * somebody picks become removable rows under the search box, each carrying a
 * hidden input, and the whole set is submitted at once. In the admin the search
 * box sits in an inline row and a pick fills that row's own fields.
 *
 * Nothing here is load-bearing. Without it the public form falls back to a
 * textarea, one address per line, and the admin falls back to typing an address
 * and letting the server look it up on save. The server validates and stores
 * either way, so this script only ever makes the job easier.
 *
 * On not pestering the geocoder: nothing is asked until typing has stopped for
 * `data-debounce` milliseconds, nothing shorter than `data-min-chars` is asked
 * about at all, the same query is never asked twice in a row, and an outstanding
 * request is abandoned the moment a newer one starts. Somebody typing an address
 * at speed produces one request, not one per letter — and the server caches the
 * answer, so the second person to type it produces none.
 */

(function () {
  "use strict";

  var ENDPOINT_FAILURE_QUIET_MS = 20000;

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        fn.apply(null, args);
      }, wait);
    };
  }

  /* A search box that offers suggestions and hands the chosen one to `onPick`.
   *
   * Everything about the list — building it, the keyboard, when to ask, when to
   * stay quiet — lives here, so the two ways of storing a pick differ only in
   * the callback. */
  function suggest(input, options) {
    var results = options.results;
    var endpoint = options.endpoint;
    var minChars = options.minChars;
    var lastQuery = null;
    var quietUntil = 0;
    var controller = null;
    var places = [];
    var active = -1;

    function toggled(open) {
      if (options.onToggle) options.onToggle(open);
    }

    function close() {
      results.hidden = true;
      results.innerHTML = "";
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      places = [];
      active = -1;
      toggled(false);
    }

    function highlight(index) {
      var items = results.children;
      for (var i = 0; i < items.length; i++) {
        items[i].classList.toggle("is-active", i === index);
        items[i].setAttribute("aria-selected", i === index ? "true" : "false");
      }
      active = index;
      if (index >= 0 && items[index]) {
        input.setAttribute("aria-activedescendant", items[index].id);
        if (items[index].scrollIntoView) {
          items[index].scrollIntoView({ block: "nearest" });
        }
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function open(found) {
      places = found;
      results.innerHTML = "";
      found.forEach(function (place, index) {
        var item = document.createElement("li");
        item.id = input.id + "_option_" + index;
        item.className = "address-picker__result";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", "false");
        item.textContent = place.label;
        // mousedown, not click: blur would close the list first.
        item.addEventListener("mousedown", function (event) {
          event.preventDefault();
          choose(index);
        });
        results.appendChild(item);
      });
      results.hidden = found.length === 0;
      input.setAttribute("aria-expanded", found.length ? "true" : "false");
      highlight(found.length ? 0 : -1);
      toggled(found.length > 0);
    }

    function choose(index) {
      var place = places[index];
      if (!place) return;
      // Whatever was typed is kept as the query: it is the wording somebody used,
      // and the label is what the geocoder called it. Both are worth having.
      place.query = input.value.trim() || place.label;
      close();
      // Quiet, so that clearing or refilling the box does not immediately ask
      // about whatever is left in it.
      quietUntil = Date.now() + 400;
      options.onPick(place);
    }

    var ask = debounce(function (query) {
      if (query === lastQuery || Date.now() < quietUntil) return;
      lastQuery = query;
      if (controller) controller.abort();
      controller = window.AbortController ? new window.AbortController() : null;

      window
        .fetch(endpoint + "?q=" + encodeURIComponent(query), {
          headers: { Accept: "application/json" },
          signal: controller ? controller.signal : undefined,
          credentials: "same-origin"
        })
        .then(function (response) {
          if (!response.ok) throw new Error(response.status);
          return response.json();
        })
        .then(function (body) {
          if (input.value.trim() !== query) return; // they kept typing
          open(body.results || []);
          if (body.unavailable && options.onUnavailable) options.onUnavailable();
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") return;
          // Stay quiet for a while rather than retrying into a wall on every
          // keystroke. Typing still works; it just stops suggesting.
          quietUntil = Date.now() + ENDPOINT_FAILURE_QUIET_MS;
          close();
          if (options.onUnavailable) options.onUnavailable();
        });
    }, options.debounce);

    input.addEventListener("input", function () {
      var query = input.value.trim();
      if (query.length < minChars) {
        close();
        lastQuery = null;
        return;
      }
      ask(query);
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (results.hidden || !places.length) return;
        event.preventDefault();
        var step = event.key === "ArrowDown" ? 1 : -1;
        highlight((active + step + places.length) % places.length);
      } else if (event.key === "Enter") {
        // Enter picks the highlighted suggestion rather than submitting the form.
        // Submitting halfway through choosing an address is never what anybody
        // meant by it.
        if (!results.hidden && active >= 0) {
          event.preventDefault();
          choose(active);
        } else if (options.onEnter) {
          event.preventDefault();
          options.onEnter();
        }
      } else if (event.key === "Escape") {
        if (!results.hidden) {
          event.stopPropagation();
          close();
        }
      }
    });

    input.addEventListener("blur", function () {
      window.setTimeout(close, 120);
    });

    return { close: close };
  }

  /* --- The public forms: picks become rows under the box ------------------- */

  function chipPicker(root) {
    if (root.dataset.addressReady) return;
    root.dataset.addressReady = "1";

    var input = root.querySelector("[data-address-input]");
    var results = root.querySelector("[data-address-results]");
    var chosen = root.querySelector("[data-address-chosen]");
    var status = root.querySelector("[data-address-status]");
    var addButton = root.querySelector("[data-address-add]");
    if (!input || !results || !chosen) return;

    var field = root.dataset.field;
    var max = parseInt(root.dataset.max, 10) || 10;

    function count() {
      return chosen.querySelectorAll("[data-address-place]").length;
    }

    function say(message) {
      if (status) status.textContent = message;
    }

    function wire(row) {
      var remove = row.querySelector("[data-address-remove]");
      if (!remove) return;
      remove.addEventListener("click", function () {
        var label = row.querySelector(".address-picker__label");
        row.remove();
        say((label ? label.textContent : "That place") + " removed.");
        input.focus();
      });
    }

    function alreadyHave(payload) {
      var existing = chosen.querySelectorAll('input[name="' + field + '"]');
      for (var i = 0; i < existing.length; i++) {
        if (existing[i].value === payload) return true;
      }
      return false;
    }

    function add(place) {
      if (count() >= max) {
        say("That is as many places as this form takes. Describe the rest in your description.");
        return;
      }
      var payload = JSON.stringify(place);
      if (alreadyHave(payload)) {
        say("That one is already on the list.");
        input.value = "";
        return;
      }

      var label = place.label || place.query;
      var row = document.createElement("li");
      row.className = "address-picker__place";
      row.setAttribute("data-address-place", "");

      var text = document.createElement("span");
      text.className = "address-picker__label";
      text.textContent = label;

      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = field;
      hidden.value = payload;

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "address-picker__remove";
      remove.setAttribute("data-address-remove", "");
      remove.setAttribute("aria-label", "Remove " + label);
      remove.textContent = "×";

      row.appendChild(text);
      row.appendChild(hidden);
      row.appendChild(remove);
      chosen.appendChild(row);
      wire(row);

      input.value = "";
      say(label + " added. " + count() + " so far.");
    }

    function addTyped() {
      var typed = input.value.trim();
      if (!typed) {
        say("Type an address first.");
        return;
      }
      // No coordinates, on purpose: this is the escape hatch for a place no
      // geocoder knows, and the server treats it as an address waiting to be
      // looked up rather than one that failed.
      add({ query: typed });
    }

    Array.prototype.forEach.call(chosen.querySelectorAll("[data-address-place]"), wire);
    if (addButton) addButton.addEventListener("click", addTyped);

    suggest(input, {
      results: results,
      endpoint: root.dataset.endpoint,
      minChars: parseInt(root.dataset.minChars, 10) || 3,
      debounce: parseInt(root.dataset.debounce, 10) || 450,
      onPick: add,
      onEnter: addTyped,
      onUnavailable: function () {
        say("Address suggestions are unavailable just now. Type the address and press + to add it.");
      }
    });
  }

  /* --- The admin: a pick fills the row it was made in --------------------- */

  function rowPicker(input) {
    if (input.dataset.addressReady) return;
    input.dataset.addressReady = "1";

    var row = input.closest("tr") || input.parentNode;
    var results = document.createElement("ul");
    results.id = input.id + "_results";
    results.className = "address-picker__results";
    results.setAttribute("role", "listbox");
    results.hidden = true;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", results.id);
    input.setAttribute("autocomplete", "off");
    input.parentNode.appendChild(results);
    input.parentNode.classList.add("address-picker__cell");

    /* The row's own inputs, found by the suffix Django gives them:
       locations-0-query, locations-0-label, and so on. */
    function sibling(suffix) {
      var name = input.name.replace(/-query$/, "-" + suffix);
      return row.querySelector('[name="' + name + '"]');
    }

    /* django-unfold wraps an inline table in a horizontal scroll container, and a
       scroll container clips on both axes whatever its author intended — so the
       suggestions for the bottom row would be cut off by the edge of the table.
       Letting it overflow only while the list is open costs nothing: nobody
       scrolls a table sideways during the second they are choosing an address. */
    var wrapper = input.closest(".formset-wrapper");

    suggest(input, {
      results: results,
      endpoint: input.dataset.endpoint,
      minChars: parseInt(input.dataset.minChars, 10) || 3,
      debounce: parseInt(input.dataset.debounce, 10) || 450,
      onToggle: function (open) {
        if (wrapper) wrapper.classList.toggle("address-picker-open", open);
      },
      onPick: function (place) {
        input.value = place.query || place.label;
        var fill = { label: place.label, latitude: place.latitude, longitude: place.longitude };
        Object.keys(fill).forEach(function (suffix) {
          var target = sibling(suffix);
          if (target && fill[suffix] !== undefined && fill[suffix] !== null) {
            target.value = fill[suffix];
          }
        });
        // Everything else the geocoder said, carried across whole so that the
        // city and postcode are not quietly lost just because the inline does
        // not show a column for them.
        var picked = sibling("picked");
        if (picked) picked.value = JSON.stringify(place);
      }
    });
  }

  function start() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-address-picker]"),
      chipPicker
    );
    Array.prototype.forEach.call(document.querySelectorAll("[data-address-row]"), rowPicker);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  // Inline rows the admin adds after the page has loaded. Django fires this on
  // the document through its own jQuery; listening for the native event as well
  // costs nothing and covers both.
  document.addEventListener("formset:added", start);
  if (window.django && window.django.jQuery) {
    window.django.jQuery(document).on("formset:added", start);
  }
})();
