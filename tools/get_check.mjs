// Checks the download button's choice of file (demo/get.js) for the computers people really have.
//   node tools/get_check.mjs
import { createRequire } from "node:module";
import assert from "node:assert/strict";
const { pick, FILES } = createRequire(import.meta.url)("../demo/get.js");

const UA = {
  win: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15",
  ipad: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15",
  iphone: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
  android: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36",
  linux: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
};
const cases = [
  ["Windows Chrome",          { ua: UA.win, platform: "Windows" }, "windows", FILES.windows],
  ["Windows Firefox",         { ua: UA.win.replace("Chrome/130 Safari/537.36", "Gecko/20100101 Firefox/130"), platform: "Win32" }, "windows", FILES.windows],
  ["Mac Chrome, Apple chip",  { ua: UA.mac, platform: "macOS", arch: "arm" }, "mac", FILES.mac],
  ["Mac Chrome, Intel",       { ua: UA.mac, platform: "macOS", arch: "x86" }, "mac", FILES.macIntel],
  ["Mac Firefox, Apple GPU name", { ua: UA.mac, platform: "MacIntel", gpu: "Apple M2" }, "mac", FILES.mac],
  ["Mac Firefox, Intel GPU name", { ua: UA.mac, platform: "MacIntel", gpu: "Intel(R) Iris(TM) Plus Graphics 640" }, "mac", FILES.macIntel],
  ["Mac Safari, hides the chip",  { ua: UA.mac, platform: "MacIntel", gpu: "Apple GPU" }, "mac", FILES.mac],
  ["Mac, nothing known",      { ua: UA.mac, platform: "MacIntel" }, "mac", FILES.mac],
  ["iPad (says it is a Mac)", { ua: UA.ipad, platform: "MacIntel", touch: 5 }, "other"],
  ["iPhone",                  { ua: UA.iphone, platform: "iPhone", touch: 5 }, "other"],
  ["Android",                 { ua: UA.android, platform: "Linux armv8l", touch: 5 }, "other"],
  ["Linux",                   { ua: UA.linux, platform: "Linux x86_64" }, "other"],
  ["no information at all",   {}, "other"],
];
let bad = 0;
for (const [name, env, os, file] of cases) {
  const r = pick(env);
  try {
    assert.equal(r.os, os);
    if (file) assert.equal(r.file, file);
    console.log("ok   ", name, "->", r.os, r.file ? r.file.split("/").pop() : "");
  } catch (e) { bad++; console.log("FAIL ", name, "->", JSON.stringify(r)); }
}
// Safari's unknown chip offers the Intel file as the other choice.
assert.equal(pick({ ua: UA.mac, platform: "MacIntel" }).alt, FILES.macIntel);
process.exit(bad ? 1 : 0);
