/**
 * export_diagram.mjs - render one Excalidraw scene file to a standalone SVG.
 *
 *     node tools/export_diagram.mjs <in.excalidraw> <out.svg>
 *
 * Excalidraw's exporter is browser code: it reads `window`, `document`,
 * `location`, `devicePixelRatio` and friends while the module is still being
 * evaluated. So we stand up a jsdom window, mirror it onto globalThis, and
 * only then import @excalidraw/utils. Importing it first throws
 * "ReferenceError: window is not defined" before any of our code runs, which
 * is why the import below is dynamic.
 *
 * The output is a white-background SVG with 24px of padding, no embedded
 * scene JSON (the .excalidraw file next to it is the source of truth) and no
 * embedded font files. Font embedding is what turns a 20KB diagram into a
 * 900KB one, so it stays off; the SVG names the font family and any viewer
 * falls back to its own sans-serif, which is fine for boxes and labels.
 *
 * Normally you do not call this directly - `python tools/export_diagrams.py`
 * decides what is stale and calls it. See tools/README.md.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const EXPORT_PADDING = 24;

function usage(msg) {
  process.stderr.write(
    `${msg}\n\nusage: node tools/export_diagram.mjs <in.excalidraw> <out.svg>\n`);
  process.exit(2);
}

function installBrowserGlobals() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    pretendToBeVisual: true,
    url: "https://excalidraw.com/",
  });
  const w = dom.window;
  // Mirror the whole window rather than shimming names one at a time: the
  // bundle reads bare globals we cannot enumerate ahead of time.
  for (const key of Object.getOwnPropertyNames(w)) {
    if (key in globalThis && key !== "location" && key !== "navigator") continue;
    const desc = Object.getOwnPropertyDescriptor(w, key);
    if (!desc) continue;
    try {
      Object.defineProperty(globalThis, key, { ...desc, configurable: true });
    } catch {
      /* a read-only host global we cannot shadow; nothing writes to these */
    }
  }
  for (const [name, value] of [["window", w], ["self", w]]) {
    Object.defineProperty(globalThis, name, { value, configurable: true });
  }
  globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  w.EXCALIDRAW_EXPORT_SOURCE = "https://excalidraw.com";
  return w;
}

async function main(argv) {
  const [input, output] = argv;
  if (!input || !output) usage("expected an input and an output path");

  let scene;
  try {
    scene = JSON.parse(readFileSync(input, "utf8"));
  } catch (err) {
    usage(`could not read ${input} as Excalidraw JSON: ${err.message}`);
  }
  const elements = (scene.elements || []).filter((el) => !el.isDeleted);
  if (!elements.length) usage(`${input} has no elements to export`);

  installBrowserGlobals();
  const { exportToSvg } = await import("@excalidraw/utils");

  const svg = await exportToSvg({
    elements,
    files: scene.files || null,
    appState: {
      ...(scene.appState || {}),
      exportBackground: true,
      exportWithDarkMode: false,
      exportEmbedScene: false,          // the .excalidraw file is the source
      exportScale: 1,
      viewBackgroundColor:
        (scene.appState && scene.appState.viewBackgroundColor) || "#ffffff",
      exportPadding: EXPORT_PADDING,
    },
    exportPadding: EXPORT_PADDING,
    // Keep the file small: no base64 font faces, no scene payload.
    skipInliningFonts: true,
  });

  writeFileSync(output, svg.outerHTML, "utf8");
  process.stdout.write(`${output}\n`);
}

await main(process.argv.slice(2));
