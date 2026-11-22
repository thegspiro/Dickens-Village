/*
 * Catalog lookup for the Add / Edit piece form.
 *
 * Type an item number (or part of a name), pick a match, and the form's
 * name, item number, category, series and years are filled in from the
 * reference catalog. Plain JavaScript, no dependencies. All text from the
 * server is inserted with textContent, never as HTML.
 */
(function () {
  "use strict";

  var MIN_CHARS = 2;
  var DEBOUNCE_MS = 200;

  function formatYears(r) {
    var issued = r.year_issued ? String(r.year_issued) : "?";
    var retired = r.is_current ? "Current" : r.year_retired ? String(r.year_retired) : "?";
    return issued + "–" + retired;
  }

  function describe(r) {
    var parts = [r.item_number || "No number", r.name, formatYears(r)];
    if (r.parent_name) {
      parts.push("part of “" + r.parent_name + "”");
    }
    return parts.join(" · ");
  }

  /* Only same-site paths are used as image sources. */
  function isLocalUrl(url) {
    return typeof url === "string" && url.charAt(0) === "/" && url.charAt(1) !== "/" && url.indexOf("\\") === -1;
  }

  /* Second line of a suggestion: which list it is from, plus any notes. */
  function detailLine(r) {
    var parts = [r.list === "accessories" ? "Accessories list" : "Lighted list"];
    // For set pieces the first line already names the set, so skip the repeat.
    if (r.details && !(r.parent_name && r.details === 'Part of "' + r.parent_name + '"')) {
      parts.push(r.details);
    }
    return parts.join(" · ");
  }

  /* Form field id -> value to set. Fields with no catalog value are left alone. */
  function fieldValues(r) {
    var values = { name: r.name, item_number: r.item_number || "" };
    if (r.category) values.category = r.category;
    if (r.series) values.series = r.series;
    if (r.year_issued) values.year_introduced = String(r.year_issued);
    if (r.year_retired) values.year_retired = String(r.year_retired);
    else if (r.is_current) values.year_retired = "";
    return values;
  }

  function init(doc) {
    var section = doc.querySelector(".catalog-lookup");
    var input = doc.getElementById("catalog-lookup");
    var list = doc.getElementById("catalog-results");
    var status = doc.getElementById("catalog-status");
    var hiddenId = doc.getElementById("catalog_id");
    var linked = doc.getElementById("catalog-linked");
    var linkedText = doc.getElementById("catalog-linked-text");
    var unlink = doc.getElementById("catalog_unlink");
    if (!section || !input || !list || !hiddenId) return;

    var searchUrl = section.getAttribute("data-search-url");
    var results = [];
    var active = -1;
    var timer = null;
    var requestSeq = 0;

    section.hidden = false;

    function setStatus(text) {
      if (status) status.textContent = text;
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function highlight(index) {
      var items = list.querySelectorAll("[role=option]");
      for (var i = 0; i < items.length; i++) {
        items[i].setAttribute("aria-selected", i === index ? "true" : "false");
      }
      active = index;
      if (index >= 0 && items[index]) {
        input.setAttribute("aria-activedescendant", items[index].id);
        if (items[index].scrollIntoView) items[index].scrollIntoView({ block: "nearest" });
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function render() {
      while (list.firstChild) list.removeChild(list.firstChild);
      var anyThumbs = results.some(function (r) { return isLocalUrl(r.thumb_url); });
      results.forEach(function (r, i) {
        var li = doc.createElement("li");
        li.id = "catalog-option-" + i;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        if (anyThumbs) {
          // Keep rows aligned: a blank square where an entry has no photo yet.
          li.className = "has-thumb";
          if (isLocalUrl(r.thumb_url)) {
            var img = doc.createElement("img");
            img.className = "combo-thumb";
            img.alt = "";
            img.loading = "lazy";
            img.src = r.thumb_url;
            li.appendChild(img);
          } else {
            var blank = doc.createElement("span");
            blank.className = "combo-thumb-empty";
            li.appendChild(blank);
          }
        }
        // First line: item number, then the name in bold so pieces that
        // share a number are easy to tell apart.
        var title = doc.createElement("span");
        title.className = "combo-title";
        var number = doc.createElement("span");
        number.className = "combo-number";
        number.textContent = r.item_number || "No number";
        var name = doc.createElement("strong");
        name.textContent = r.name;
        var rest = doc.createElement("span");
        rest.textContent = " · " + formatYears(r) + (r.parent_name ? " · part of “" + r.parent_name + "”" : "");
        title.appendChild(number);
        title.appendChild(doc.createTextNode(" · "));
        title.appendChild(name);
        title.appendChild(rest);
        li.appendChild(title);
        var detail = doc.createElement("span");
        detail.className = "combo-detail";
        detail.textContent = detailLine(r);
        li.appendChild(detail);
        li.addEventListener("mousedown", function (event) {
          event.preventDefault(); // keep focus in the input
          choose(i);
        });
        list.appendChild(li);
      });
      list.hidden = results.length === 0;
      input.setAttribute("aria-expanded", results.length ? "true" : "false");
      active = -1;
    }

    function choose(index) {
      var r = results[index];
      if (!r) return;
      var values = fieldValues(r);
      Object.keys(values).forEach(function (id) {
        var field = doc.getElementById(id);
        if (field) field.value = values[id];
      });
      hiddenId.value = String(r.id);
      if (unlink) unlink.checked = false;
      if (linked && linkedText) {
        linkedText.textContent = "Linked to catalog: " + describe(r);
        linked.hidden = false;
      }
      input.value = "";
      close();
      setStatus("Filled in from the catalog: " + describe(r) + ". Check the fields below before saving.");
      var name = doc.getElementById("name");
      if (name) name.focus();
    }

    function search(query) {
      var seq = ++requestSeq;
      fetch(searchUrl + "?q=" + encodeURIComponent(query), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (response) {
          var type = response.headers.get("Content-Type") || "";
          if (response.redirected || type.indexOf("application/json") === -1) {
            throw new Error("session");
          }
          if (!response.ok) throw new Error("http");
          return response.json();
        })
        .then(function (data) {
          if (seq !== requestSeq) return; // a newer search has started
          results = (data && data.results) || [];
          render();
          setStatus(
            results.length
              ? results.length + (results.length === 1 ? " match" : " matches") + ". Use the arrow keys or click to pick one."
              : "No catalog entries match “" + query + "”."
          );
        })
        .catch(function (err) {
          if (seq !== requestSeq) return;
          results = [];
          render();
          setStatus(
            err && err.message === "session"
              ? "Your session has expired. Reload the page and log in again."
              : "The catalog could not be searched just now. You can still fill in the form by hand."
          );
        });
    }

    input.addEventListener("input", function () {
      var query = input.value.trim();
      if (timer) clearTimeout(timer);
      if (query.length < MIN_CHARS) {
        requestSeq++;
        results = [];
        render();
        setStatus("");
        return;
      }
      timer = setTimeout(function () {
        search(query);
      }, DEBOUNCE_MS);
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        if (results.length) {
          list.hidden = false;
          input.setAttribute("aria-expanded", "true");
          highlight(Math.min(active + 1, results.length - 1));
        }
        event.preventDefault();
      } else if (event.key === "ArrowUp") {
        if (results.length) highlight(Math.max(active - 1, 0));
        event.preventDefault();
      } else if (event.key === "Enter") {
        // Never submit the whole form from the lookup box.
        event.preventDefault();
        if (!list.hidden && results.length) choose(active >= 0 ? active : 0);
      } else if (event.key === "Escape") {
        close();
      }
    });

    input.addEventListener("blur", function () {
      setTimeout(close, 100);
    });

    if (unlink && linked) {
      unlink.addEventListener("change", function () {
        if (unlink.checked) setStatus("The catalog link will be removed when you save.");
      });
    }
  }

  var api = { describe: describe, detailLine: detailLine, isLocalUrl: isLocalUrl, fieldValues: fieldValues, formatYears: formatYears, init: init };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        init(document);
      });
    } else {
      init(document);
    }
  }
})();
