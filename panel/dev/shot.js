// Panel screenshots with interactions, through the Chrome DevTools Protocol (no dependencies).
//   node --experimental-websocket shot.js <url> <output.png> <width>x<height> [js-to-run] [wait-ms]
const { spawn } = require("child_process");
let [url, out, size = "1440x900", script = "", wait = "2500"] = process.argv.slice(2);
if (script.startsWith("@")) script = require("fs").readFileSync(script.slice(1), "utf8");
const [w, h] = size.split("x").map(Number);
const port = 9300 + Math.floor(Math.random() * 500);
const chrome = spawn("chromium", ["--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${port}`,
  `--window-size=${w},${h}`, "--user-data-dir=/tmp/shot-profile-" + port, ...(process.env.CHROME_ARGS || "").split(" ").filter(Boolean), "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  let ws;
  for (let i = 0; i < 50; i++) {
    try { const t = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = t.find(x => x.type === "page"); if (p) { ws = new WebSocket(p.webSocketDebuggerUrl); break; } } catch (e) { }
    await sleep(200);
  }
  await new Promise(r => ws.onopen = r);
  let id = 0; const pending = {};
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending[d.id]) { pending[d.id](d); delete pending[d.id]; } };
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url });
  await sleep(+wait);
  for (const step of script.split(";;").filter(Boolean)) {
    const r = await send("Runtime.evaluate", { expression: step, awaitPromise: true });
    if (r.result?.exceptionDetails) console.error("error in step:", step, r.result.exceptionDetails.text);
    await sleep(900);
  }
  if (process.env.I18N) {
    const r = await send("Runtime.evaluate", { expression: "JSON.stringify([...(window.__i18nMissing || [])])" });
    require("fs").writeFileSync(out + ".missing.json", r.result.result.value || "[]");
  }
  const shot = await send("Page.captureScreenshot", { format: "png" });
  require("fs").writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  console.log("ok", out);
  ws.close(); chrome.kill(); process.exit(0);
})().catch(e => { console.error(e); chrome.kill(); process.exit(1); });
