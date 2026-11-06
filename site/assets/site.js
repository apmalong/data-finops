// Windows and macOS/Linux tabs for code examples (#173, spec 07 "Layout").
// build puts both versions of every command block in a .shell-tabs group. Without this script
// both show, each labelled, so every page works. With it, one choice shows one version in every
// group on every page: the reader's OS first, then whatever they last picked.
(function () {
  "use strict";
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
