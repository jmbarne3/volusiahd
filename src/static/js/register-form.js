/* The registration form, narrowed to the kind of program being registered.
 *
 * Thirteen kinds of program, one form, and about forty questions between them.
 * Showing all forty to everybody would be a form most people abandon, so the
 * heading somebody picks decides what stays on screen. Two attributes carry
 * everything this needs, both written by the server:
 *
 *   data-asked-by   on an input — the headings that ask this question at all
 *   data-categories on an option — the headings offered this particular answer
 *
 * Both are lists of category slugs, and the chosen heading's slug rides along on
 * its own <option> as data-slug. Nothing here is load-bearing. With the script
 * switched off every question is shown, and the server decides what it asked
 * about rather than trusting the shape of what came back — see
 * `questions.py` and `ProgramRegistrationForm._forget_unasked`.
 *
 * On not clearing what it hides: a hidden field keeps whatever was typed in it.
 * Somebody who fills in their meeting days, realises they picked the wrong
 * heading and corrects it should find their work still there, and the server
 * drops answers to questions it did not ask anyway. Clearing would be us
 * protecting a rule that is already enforced somewhere it cannot be bypassed,
 * at the cost of the one mistake this form makes easy to recover from.
 */

(function () {
  "use strict";

  function slugOf(select) {
    var option = select.options[select.selectedIndex];
    return (option && option.dataset.slug) || "";
  }

  function offeredTo(element, slug) {
    // No heading chosen yet: show everything, which is what the server does too.
    if (slug === "") return true;
    var attribute = element.getAttribute("data-asked-by") || element.getAttribute("data-categories");
    if (attribute === null) return true;
    return attribute.split(" ").indexOf(slug) !== -1;
  }

  /* A <select multiple> narrowed by removing options, which is why the full set
   * is held here rather than read back off the element: widening again has to put
   * them back in their original order. */
  function optionNarrower(select) {
    var all = Array.prototype.slice.call(select.options);
    return function (slug) {
      var offered = all.filter(function (option) {
        return slug !== "" && offeredTo(option, slug);
      });
      // An option that is no longer on offer cannot stay chosen.
      all.forEach(function (option) {
        if (offered.indexOf(option) === -1) option.selected = false;
      });
      select.textContent = "";
      offered.forEach(function (option) {
        select.appendChild(option);
      });
      return offered.length;
    };
  }

  function start() {
    var form = document.querySelector("[data-scoped-form]");
    if (!form) return;
    var category = form.querySelector("select[name='category']");
    if (!category) return;

    var tagSelect = form.querySelector("select[data-tag-select]");
    var tagHint = form.querySelector("[data-tag-hint]");
    var narrowTags = tagSelect ? optionNarrower(tagSelect) : null;
    var jq = window.jQuery;
    var enhanced = tagSelect && jq && typeof jq.fn.select2 === "function";

    if (enhanced) {
      // `size` only helps the native fallback; Select2 would honour it as a height.
      tagSelect.removeAttribute("size");
      jq(tagSelect).select2({
        width: "100%",
        placeholder: tagSelect.dataset.placeholder || "",
        closeOnSelect: false
      });
    }

    var scoped = Array.prototype.slice.call(form.querySelectorAll("[data-asked-by]"));
    // Radios and checkboxes carry the attribute on every one of their inputs, so
    // the same field turns up several times. Once is enough.
    var scopedFields = [];
    scoped.forEach(function (input) {
      var field = input.closest(".field");
      if (field && scopedFields.indexOf(field) === -1) scopedFields.push(field);
      if (field && !field.dataset.askedBy) field.dataset.askedBy = input.getAttribute("data-asked-by");
    });

    // Individual answers inside a tag question, each offered to its own headings.
    var answers = Array.prototype.slice.call(
      form.querySelectorAll(".field input[data-categories]")
    );

    function refresh() {
      var slug = slugOf(category);

      scopedFields.forEach(function (field) {
        field.hidden = !offeredTo(field, slug);
      });

      answers.forEach(function (input) {
        var option = input.closest("label") || input;
        var shown = offeredTo(input, slug);
        option.hidden = !shown;
        if (!shown) input.checked = false;
      });

      // A question every one of whose answers has just been hidden is a question
      // with nothing to answer.
      document.querySelectorAll("[data-answer-field]").forEach(function (field) {
        var visible = field.querySelectorAll("input[data-categories]:not([hidden])");
        var anyShown = Array.prototype.some.call(visible, function (input) {
          var option = input.closest("label");
          return !option || !option.hidden;
        });
        field.hidden = !anyShown;
      });

      if (narrowTags) {
        var offered = narrowTags(slug);
        var tagField = tagSelect.closest(".field");
        if (tagField) tagField.hidden = offered === 0;
        if (tagHint) tagHint.hidden = slug !== "" && offered !== 0;
        if (enhanced) jq(tagSelect).trigger("change.select2");
      }

      // Last, because it reads the result of everything above: a fieldset whose
      // every question is hidden should take its legend and its hints with it.
      Array.prototype.forEach.call(form.querySelectorAll("fieldset"), function (fieldset) {
        var fields = fieldset.querySelectorAll(".field");
        if (!fields.length) return;
        var anyShown = Array.prototype.some.call(fields, function (field) {
          return !field.hidden;
        });
        fieldset.hidden = !anyShown;
      });
    }

    category.addEventListener("change", refresh);
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
