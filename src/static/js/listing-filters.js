/* The listing sidebar: filters that apply themselves, and a drawer on phones.
 *
 * Without this script the sidebar is an ordinary GET form with an Apply button,
 * and every filter still works. With it, ticking a box or picking a day applies
 * at once and the button hides itself, so there is one way to search rather
 * than a Search button and an Apply button that do the same thing.
 *
 * It also folds the category list to its first five, with a toggle under it.
 *
 * The two text boxes are the exception. The search box applies on Enter, as
 * any search box does, and the place box applies when a suggestion is picked
 * (address-picker.js handles that) — submitting on every keystroke would reload
 * the page under somebody's fingers.
 */

(function () {
  "use strict";

  // Matches the breakpoint in site.css where the sidebar becomes a drawer.
  var NARROW = "(max-width: 56rem)";

  function autoApply(form) {
    var actions = form.querySelector("[data-apply-actions]");
    if (actions) actions.hidden = true;

    function submit() {
      if (form.requestSubmit) form.requestSubmit();
      else form.submit();
    }

    form.addEventListener("change", function (event) {
      var target = event.target;
      if (!target || !target.name) return;
      // The text boxes have their own moment to apply; see above. A number box
      // fires `change` on blur or Enter, which is the right moment for an age.
      if (target.type === "search" || target.type === "text") return;
      // Changing the distance with no place chosen would reload for nothing.
      if (target.name === "within" && !form.querySelector('[name="near"]').value.trim()) return;
      submit();
    });
  }

  /* Open on a wide screen, where the sidebar is always shown; closed on a
   * narrow one, so a phone sees programs before it sees filters. Rendered open,
   * so with no script the filters are never hidden behind a control. */
  function drawer(details) {
    if (!window.matchMedia) return;
    var query = window.matchMedia(NARROW);
    function fit() {
      details.open = !query.matches;
    }
    fit();
    if (query.addEventListener) query.addEventListener("change", fit);
    else if (query.addListener) query.addListener(fit);
  }

  /* A list that shows its first few items and folds the rest behind a toggle
   * underneath it. Folded here rather than in the markup, so that with no
   * script every item shows and there is no toggle that does nothing. */
  function fold(section) {
    var toggle = section.querySelector("[data-fold-toggle]");
    if (!toggle) return;

    function set(open) {
      section.classList.toggle("is-folded", !open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? toggle.dataset.fewer : toggle.dataset.more;
    }

    set(section.hasAttribute("data-fold-open"));
    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      set(section.classList.contains("is-folded"));
    });
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-fold]"), fold);
    Array.prototype.forEach.call(document.querySelectorAll("form[data-auto-apply]"), autoApply);
    Array.prototype.forEach.call(document.querySelectorAll("[data-filter-drawer]"), drawer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
