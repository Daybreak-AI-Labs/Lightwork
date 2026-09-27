/* Small progressive enhancements. Every page works without this file. */
(function () {
  "use strict";
  // "Copy" buttons: <button data-copy="element-id">. Reports the result in a
  // polite live region next to the button.
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-copy]") : null;
    if (!button) return;
    var source = document.getElementById(button.getAttribute("data-copy"));
    var status = document.getElementById(button.getAttribute("data-copy") + "-status");
    if (!source) return;
    var text = source.textContent.trim();
    function done(message) { if (status) status.textContent = message; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { done("Copied."); },
        function () { done("Couldn't copy automatically. Select the text and copy it."); });
    } else {
      done("Couldn't copy automatically. Select the text and copy it.");
    }
  });
})();
