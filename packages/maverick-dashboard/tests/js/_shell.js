"use strict";
// The shell's shared helpers (lightwork-ui.js: mvFetchJSON, mvStatus,
// mvErrorText …) load before every page script, as in base.html. Page tests
// call shell(window) before evaluating the page script.
const fs = require("node:fs");
const path = require("node:path");
const ui = fs.readFileSync(path.resolve(__dirname, "../../maverick_dashboard/static/lightwork-ui.js"), "utf8");
function shell(w) {
  if (typeof w.matchMedia !== "function") w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  w.eval(ui);
  return w;
}
module.exports = { shell, ui };
