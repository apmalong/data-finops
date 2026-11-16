// The site's search (#274): build writes assets/search-index.json, one entry for each page
// (path, title, section, headings, text). search() is a plain function, so Node tests run the
// same code as the browser. A page is found when it holds every word of the query, as text:
// no word is a pattern. A word in the title counts 3, in a heading 2, in the text 1.
var DataFinopsSearch = (function () {
  "use strict";
  var MAX_RESULTS = 10;
  var SNIPPET = 160;

  function words(query) {
    return String(query).toLowerCase().split(/\s+/).filter(Boolean);
  }

  function score(entry, terms) {
    var title = entry.title.toLowerCase();
    var headings = entry.headings.join(" ").toLowerCase();
    var text = entry.text.toLowerCase();
    var total = 0;
    for (var i = 0; i < terms.length; i += 1) {
      var term = terms[i];
      if (title.indexOf(term) !== -1) total += 3;
      else if (headings.indexOf(term) !== -1) total += 2;
      else if (text.indexOf(term) !== -1) total += 1;
      else return 0;
    }
    return total;
  }

  function snippet(text, term) {
    if (text.length <= SNIPPET) return text;
    var at = Math.max(0, text.toLowerCase().indexOf(term) - 60);
    var part = text.slice(at, at + SNIPPET);
    return (at > 0 ? "… " : "") + part + (at + SNIPPET < text.length ? " …" : "");
  }

  function search(index, query) {
    var terms = words(query);
    if (!terms.length) return [];
    var found = [];
    for (var i = 0; i < index.length; i += 1) {
      var points = score(index[i], terms);
      if (points) found.push({ points: points, order: i, entry: index[i] });
    }
    found.sort(function (a, b) { return b.points - a.points || a.order - b.order; });
    return found.slice(0, MAX_RESULTS).map(function (hit) {
      return {
        path: hit.entry.path,
        title: hit.entry.title,
        section: hit.entry.section,
        snippet: snippet(hit.entry.text, terms[0]),
      };
    });
  }

  return { search: search };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = DataFinopsSearch;
}

// The search box in the top bar. build stamps it hidden, so without this script it never shows.
// Results are built with textContent only, so no page text is read as HTML.
(function () {
  "use strict";
  if (typeof document === "undefined") return;
  var form = document.querySelector(".site-search");
  if (!form) return;
  var input = form.querySelector("input");
  var list = form.querySelector(".search-results");
  var root = form.getAttribute("data-root") || "./";
  var loading = null;

  function index() {
    if (!loading) {
      loading = fetch(form.getAttribute("data-index"))
        .then(function (response) { return response.ok ? response.json() : []; })
        .catch(function () { return []; });
    }
    return loading;
  }

  function show(results, query) {
    list.replaceChildren();
    if (!query.trim()) return;
    if (!results.length) {
      var none = document.createElement("li");
      none.className = "search-none";
      none.textContent = "No page holds every word.";
      list.appendChild(none);
      return;
    }
    results.forEach(function (result) {
      var item = document.createElement("li");
      var link = document.createElement("a");
      link.href = root + result.path;
      link.textContent = result.title;
      var where = document.createElement("span");
      where.className = "search-section";
      where.textContent = result.section;
      var text = document.createElement("p");
      text.textContent = result.snippet;
      item.append(link, where, text);
      list.appendChild(item);
    });
  }

  input.addEventListener("input", function () {
    var query = input.value;
    index().then(function (entries) {
      if (input.value === query) show(DataFinopsSearch.search(entries, query), query);
    });
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      input.value = "";
      list.replaceChildren();
    }
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var first = list.querySelector("a");
    if (first) window.location.href = first.href;
  });
  form.hidden = false;
  // A search can be shared as a link: ?q=words fills the box and shows the results.
  var shared = new URLSearchParams(window.location.search).get("q");
  if (shared) {
    input.value = shared;
    input.dispatchEvent(new Event("input"));
  }
})();

// Windows and macOS/Linux tabs for code examples (#173, spec 07 "Layout").
// build puts both versions of every command block in a .shell-tabs group. Without this script
// both show, each labelled, so every page works. With it, one choice shows one version in every
// group on every page: the reader's OS first, then whatever they last picked.
(function () {
  "use strict";
  if (typeof document === "undefined") return;
  var KEY = "data-finops-shell";
  var LABELS = { posix: "macOS and Linux", windows: "Windows (PowerShell)" };
  var root = document.documentElement;

  function remembered() {
    try {
      var shell = window.localStorage.getItem(KEY);
      return shell === "posix" || shell === "windows" ? shell : null;
    } catch (error) {
      return null; // storage blocked: fall back to the reader's OS
    }
  }

  function remember(shell) {
    try {
      window.localStorage.setItem(KEY, shell);
    } catch (error) {
      // storage blocked: the choice lasts for this page only
    }
  }

  function guess() {
    var platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    return /win/i.test(platform) ? "windows" : "posix";
  }

  function show(shell) {
    root.setAttribute("data-shell", shell);
    var buttons = document.querySelectorAll(".shell-tab");
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].setAttribute("aria-pressed", String(buttons[i].value === shell));
    }
  }

  var groups = document.querySelectorAll(".shell-tabs");
  for (var g = 0; g < groups.length; g += 1) {
    var bar = document.createElement("div");
    bar.className = "shell-tab-bar";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Show the commands for");
    ["posix", "windows"].forEach(function (shell) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "shell-tab";
      button.value = shell;
      button.textContent = LABELS[shell];
      button.addEventListener("click", function () {
        remember(shell);
        show(shell);
      });
      bar.appendChild(button);
    });
    groups[g].insertBefore(bar, groups[g].firstChild);
  }
  root.classList.add("shell-tabs-ready");
  show(remembered() || guess());
})();
