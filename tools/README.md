# tools - diagram export

Node-side tooling, kept in its own folder with its own `package.json` so the
Python app, the Docker image and `requirements.txt` never learn about Node.
`node_modules/` is gitignored; `package.json` and `package-lock.json` are not.

Everything here is optional. The dashboard, the tests and the pipeline all run
fine on a machine that has never installed Node - a topic without an SVG is
just a topic without a picture.

## What it does

A study diagram is authored as an Excalidraw scene at
`study/diagrams/<slug>.excalidraw` and rendered to `study/diagrams/<slug>.svg`.
The scene file is the source of truth (editable, re-openable at excalidraw.com);
the SVG is what every surface embeds - the topic page in the dashboard, the
tutor, the archive export.

```bash
cd tools && npm install          # once
python tools/export_diagrams.py  # export everything stale
```

`export_diagrams.py` walks `study/diagrams/*.excalidraw`, re-exports the ones
whose SVG is missing or older than the scene, and shells out to
`export_diagram.mjs` per file. Pass slugs to limit it, `--study DIR` to point
at another folder, `--force` to redo everything. With Node or `tools/node_modules`
missing it prints what is missing and exits 0.

## Export spike - outcome

Run 2026-09-07. **It works.** Headless export needs no browser and no React
bundle.

- **Package:** `@excalidraw/utils@0.1.3-test32` (npm `latest` is a prerelease;
  there is no stable release of this package) plus `jsdom@27` for the DOM.
- **What renders correctly** on the fixture at
  `tests/unit/fixtures/diagrams/demo.excalidraw` (two labelled rectangles with
  bound text, two bound arrows, one of them labelled): 10 `<path>` elements,
  3 `<text>` elements, both container labels, the arrow label, the red
  `#ef4444` stroke, the white background. Bound text and arrow bindings both
  survive. Output is 4.8KB.

Gotchas worth knowing before you touch `export_diagram.mjs`:

- **The bundle reads browser globals while it is being evaluated**, so
  `import "@excalidraw/utils"` at the top of the file throws
  `ReferenceError: window is not defined` before any of your setup runs. The
  import has to be dynamic, after the jsdom window is in place.
- **Shimming globals one at a time is a losing game** - it asks for `window`,
  then `navigator`, then `devicePixelRatio`, then `top`, and there is no end
  to the list. Mirror the whole jsdom window onto `globalThis` instead.
- **`window` and `navigator` are getter-only on Node's `globalThis`**, so
  `globalThis.window = w` throws. Use `Object.defineProperty`.
- **`skipInliningFonts: true` is required, not an optimisation.** With font
  inlining on, the exporter reaches for `FontFace`, which jsdom does not have,
  and the export dies with `ReferenceError: FontFace is not defined`. The
  upside is the size: no base64 font faces in the output. The SVG names the
  font family and viewers fall back to their own sans-serif, which is fine for
  boxes and labels.
- **`exportEmbedScene` stays off.** The `.excalidraw` file next to the SVG is
  the source; embedding a copy of it in the image only makes the image bigger
  and the two drift apart.

## Manual fallback

If the toolchain ever breaks, or you are on a machine without Node: open the
`.excalidraw` file at [excalidraw.com](https://excalidraw.com), then
**Export image > SVG**, and save it next to the source as `<slug>.svg`. Turn
off "Embed scene" and keep the background on. That is the whole contract - no
surface cares how the SVG got there.
