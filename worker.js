// Läuft im Hintergrund: lädt Python (Pyodide) + PyMuPDF und bereinigt die PDFs.
// Die Dateien verlassen dabei nie den Browser.
importScripts("https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js");

const FONT_URL = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSansMono.ttf";

const ready = (async () => {
  const py = await loadPyodide();
  await py.loadPackage("pymupdf");
  const [script, font] = await Promise.all([
    fetch("Biegeliste.py").then((r) => {
      if (!r.ok) throw new Error("Biegeliste.py nicht gefunden");
      return r.text();
    }),
    fetch(FONT_URL).then((r) => r.arrayBuffer()),
  ]);
  py.FS.writeFile("/home/pyodide/Biegeliste.py", script);
  py.FS.mkdirTree("/fonts");
  py.FS.writeFile("/fonts/DejaVuSansMono.ttf", new Uint8Array(font));
  await py.runPythonAsync(`
import sys
sys.path.insert(0, "/home/pyodide")
from pathlib import Path
import Biegeliste as B
B.FONT_FILE = Path("/fonts/DejaVuSansMono.ttf")
`);
  return py;
})();

ready
  .then(() => postMessage({ type: "ready" }))
  .catch((err) => postMessage({ type: "failed", error: String(err) }));

// Dateien strikt nacheinander bearbeiten (gemeinsame Arbeitsdateien und Protokoll)
let queue = Promise.resolve();
onmessage = (e) => {
  queue = queue.then(() => run(e.data));
};

async function run({ id, data, onlyOverlaps }) {
  const log = [];
  try {
    const py = await ready;
    postMessage({ type: "started", id });
    py.setStdout({ batched: (s) => log.push(s) });
    py.setStderr({ batched: (s) => log.push(s) });
    try { py.FS.unlink("/tmp/out.pdf"); } catch {}
    py.FS.writeFile("/tmp/in.pdf", new Uint8Array(data));
    py.globals.set("only_overlaps", onlyOverlaps);
    const ok = await py.runPythonAsync(
      `B.process(Path("/tmp/in.pdf"), Path("/tmp/out.pdf"), only_overlaps=only_overlaps)`
    );
    const out = ok ? py.FS.readFile("/tmp/out.pdf") : null;
    postMessage({ type: "done", id, ok, log, out }, out ? [out.buffer] : []);
  } catch (err) {
    log.push(String(err));
    postMessage({ type: "done", id, ok: false, log });
  }
}
