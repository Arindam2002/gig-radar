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

## Checking a diagram

A scene can export without a single error and still be a bad picture: a label
sitting on another label, an arrow cutting through a box it has nothing to do
with, four arrowheads landing on the same corner. `check_diagram.py` measures
the geometry so those show up before the diagram counts as done.

```bash
python tools/check_diagram.py <slug>          # report only
python tools/check_diagram.py --png <slug>    # also render <slug>.png at 2x
python tools/check_diagram.py --study DIR <slug> path/to/other.excalidraw
```

The loop, which the `illustrate` skill now requires:

1. `python tools/export_diagrams.py <slug>` writes the SVG.
2. `python tools/check_diagram.py --png <slug>` prints the findings, writes
   `<slug>.lint.json` next to the scene, and rasterises the SVG to
   `<slug>.png`.
3. **Look at the PNG.** The lint catches geometry, not meaning. Only a look
   tells you whether the picture says what the topic says.
4. Fix the scene, then run it again. Stop when the lint has zero errors and the
   picture reads.

Findings come in two severities. An **error** means the picture is wrong or
unreadable, and the exit code is 1. A **warning** is worth a look and exits 0.
`--png` needs the sibling SVG; without one it says so and carries on.

### Structural rules

| Rule | Severity | What it means |
| --- | --- | --- |
| `binding` | error | A `containerId`, `startBinding` or `endBinding` points at an element that is missing, or at one that does not list it back in `boundElements`. Half a binding is how labels and arrows drift apart later. |
| `duplicate-id` | error | Two elements share an id. |
| `out-of-bounds` | error | Coordinates are not drawable, or an element sits more than 240px clear of everything else, which means it is stranded off the side of the picture. |
| `font-too-small` | error | Font under 16. 14 is allowed only for a parenthetical (text starting with `(`) or a standalone caption under 30 characters. |
| `box-too-small` | warning | A labelled rectangle under 120x60. Smaller than that and the label has no room to breathe. A warning, not an error, because badge-sized boxes are sometimes what you want. |

### Geometry rules

Every text element is measured as its own box. A container's own label is
exempt against that container, and a label bound to an arrow is measured where
the exporter will actually put it, on the arrow's midpoint, not where the scene
file happens to store it.

| Rule | Severity | What it means |
| --- | --- | --- |
| `text-over-text` | error | Two text boxes intersect by more than 2px on both axes. |
| `text-over-shape` | error | Text sits on a shape it does not label. Zones are exempt. |
| `text-over-outline` | error | Text crosses a zone's outline: the box is neither wholly inside the zone nor wholly clear of it, allowing 3px either way. Text on a zone is fine, text on its edge is not, because the zone's stroke is drawn through the words. An elliptical zone is measured against the ellipse, not its bounding box. A zone's own label is exempt against that zone. |
| `arrow-through-shape` | error | An arrow segment crosses a shape it is not attached to, measured with a 4px inset so a line hugging an edge does not count. Checked per segment, so a multi-point arrow is caught on whichever leg does it, and dashed arrows count the same as solid ones. |
| `arrow-over-text` | error | An arrow segment crosses a text box that is not its own label. |
| `label-bound-to-arrow` | error | A text element has `containerId` pointing at an arrow. See below. |
| `arrowhead-pileup` | warning | Two or more arrowheads land on the same side of a shape within 12px of each other, so the heads merge into a blob. Spread them along the side. |
| `text-overflow` | warning | A label needs more width (or height) than its container offers, at approximate Excalifont metrics: characters * fontSize * 0.55. Approximate on purpose, so it warns rather than fails. |

A **zone** is a background band or a grouping frame rather than a solid box:
either an opacity under 100, or no fill at all. Zones exist to have things drawn
on top of them and arrows crossing their edge, so `text-over-shape` and
`arrow-through-shape` skip them.

The one thing a zone does not tolerate is a label parked on its edge, which is
what `text-over-outline` is for. Drawing on a zone is the point; landing half on
and half off it puts the stroke through the words, and no z-order or opacity
setting moves the line off them. Either pull the text fully inside the zone or
push it fully clear. When neither fits, the zone is too tight for the label:
rewrap the text onto more lines, or open a corridor by moving the zone and
whatever sits beyond it.

An arrow counts as **attached** to a shape when it is bound to it, and also
when either endpoint lands inside it (within 8px). The second case matters
because the house style has unbound pointers and brackets that clearly belong
to the box they touch, and flagging those would bury the real collisions.

### Why arrow labels must not be bound

This one is worth stating on its own, because the scene looks right in the
editor and the export is wrong.

Binding a text element to an arrow (`containerId` set to the arrow's id) makes
Excalidraw treat the text as the arrow's label. On export it re-centres the
label on the arrow's midpoint and **draws the arrow stroke straight through the
words**. Every bound arrow label comes out with a line through it. There is no
padding or alignment setting that avoids it.

So an arrow label is a free-standing text element with `containerId: null`,
placed beside the arrow: offset from the segment's midpoint, on whichever side
has more empty room, far enough clear that the label box does not touch the
line. `label-bound-to-arrow` is an error precisely so this cannot come back.

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
