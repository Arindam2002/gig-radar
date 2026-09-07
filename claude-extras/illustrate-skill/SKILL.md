---
name: illustrate
description: Draw the overview diagram for one study topic. Use when the user says "illustrate <topic>", "draw this topic", "make the diagram for <slug>", or invokes /illustrate with a slug. Reads the topic's "Mental model" section, draws it with the Excalidraw connector so the user can check and edit it in chat, then writes the scene to <PROJECT_DIR>/study/diagrams/<slug>.excalidraw and exports the SVG the dashboard and the tutor embed.
---

# Illustrate: one overview diagram per study topic

Base: `<PROJECT_DIR>/study/` - `topics/<slug>.md` is the topic, and every
technical topic file has a `## Mental model` section written as literal drawing
instructions. This skill turns that section into a picture.

Two files come out of a run, in `<PROJECT_DIR>/study/diagrams/`:

- `<slug>.excalidraw` - the scene. This is the source of truth: editable, and
  it re-opens at excalidraw.com whenever the diagram needs a change.
- `<slug>.svg` - the export. This is what every surface embeds: the topic page
  in the dashboard, `/tutor`, and the published archive site.

Resume drills (`resume-*`) get no diagram. Refuse politely and say why.

## Run

1. **Read the topic.** `<PROJECT_DIR>/study/topics/<slug>.md`. If the slug does
   not resolve to a file, list the near matches and stop. Pull out the
   `## Mental model` section and make a checklist of **every element the prose
   names** - every box, every lane, every label, every annotated arrow, every
   side box, every bottom strip. That checklist is the acceptance test for the
   diagram, so write it out before drawing anything.
2. **Draw it in chat first, if you can.** When the Excalidraw connector is
   available (a `create_view` tool), draw the scene there so the user sees it
   and can edit it in chat before anything is written to disk. Ask them to look
   at it, and fold in whatever they change. When the connector is not
   available, skip the preview entirely and go straight to the file - do not
   fake a preview, and do not stall waiting for a tool that is not there.
3. **Write the scene file** to `<PROJECT_DIR>/study/diagrams/<slug>.excalidraw`
   as a full-fidelity Excalidraw scene (see "Scene file" below). The connector's
   compact `label` shorthand does not open at excalidraw.com; expand it.
4. **Export the SVG:** `python tools/export_diagrams.py <slug>` from the project
   directory. If it says Node is missing, tell the user the one-time setup
   (`cd tools && npm install`) and the manual fallback (open the `.excalidraw`
   at excalidraw.com, Export image > SVG, save next to the source).
5. **Report** the SVG path and tick the checklist from step 1 out loud - which
   named elements made it into the diagram. Anything you dropped, say so.

## Scene file

The file is a standard Excalidraw scene, the exact shape excalidraw.com writes:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [],
  "appState": { "viewBackgroundColor": "#ffffff" },
  "files": {}
}
```

Every element needs the full property set, not a shorthand: `id`, `type`, `x`,
`y`, `width`, `height`, `angle`, `strokeColor`, `backgroundColor`, `fillStyle`,
`strokeWidth`, `strokeStyle`, `roughness`, `opacity`, `groupIds`, `frameId`,
`roundness`, `seed`, `version`, `versionNonce`, `isDeleted`, `boundElements`,
`updated`, `link`, `locked`.

**A label inside a shape is its own text element.** There is no `label`
property in a scene file. Write the text as a separate element with
`containerId` set to the shape's id, and list it back on the shape as
`"boundElements": [{"id": "<text id>", "type": "text"}]`. Text elements also
carry `text`, `originalText`, `fontSize`, `fontFamily`, `textAlign`,
`verticalAlign`, `containerId` and `lineHeight`. A label that is only a text
element sitting on top of a box will drift the moment anyone moves the box.

**Arrows carry `points` and bindings.** `points` is relative to the arrow's own
`x`/`y` and starts at `[0, 0]`. `startBinding` and `endBinding` are
`{"elementId": "<shape id>", "focus": <number>, "gap": <number>}`, and each
bound shape lists the arrow in its own `boundElements` too. An arrow label is a
text element with `containerId` set to the arrow. Set `endArrowhead` to
`"arrow"`; leave `startArrowhead` null unless the prose asks for both ends.

Standalone text (a bottom strip, a formula, a note that belongs to no shape) is
a text element with `containerId: null`.

## House style

- **Palette.** Fills: `#a5d8ff` blue, `#b2f2bb` green, `#ffd8a8` orange,
  `#d0bfff` violet, `#ffc9c9` red, `#fff3bf` yellow, `#c3fae8` teal. Strokes
  are `#1e1e1e` everywhere, with one exception: an arrow the prose explicitly
  marks red is `#ef4444`, and so is its label. Use the fills to group things
  that belong together - one colour per stage, lane or tier - not one colour
  per box.
- **Minimum font size 16.** 20 for box labels, 16 for arrow labels and
  annotations. Anything smaller is unreadable in the dashboard, which scales the
  SVG to the column width.
- **Every element the prose names must be in the diagram.** The `## Mental
  model` section is a specification, not inspiration. If it says three client
  lanes, draw three. If it says a bucket with a drip label and a capacity
  marker, draw the bucket, the drip label and the capacity marker. If something
  genuinely cannot be drawn, say which item and why rather than quietly
  dropping it.
- **Layout follows the prose.** It usually says "draw left to right", so lay
  it out left to right, with generous gaps (100px or so between stages) and
  boxes big enough for their labels. Annotations sit next to what they annotate.
- Set `"roundness": {"type": 3}` on rectangles and `{"type": 2}` on arrows for
  the usual hand-drawn look; `roughness: 1` and `strokeWidth: 2` throughout.

## Redrawing

If `study/diagrams/<slug>.svg` already exists, say so and ask whether to redraw
before overwriting. When you do redraw, edit the existing `.excalidraw` rather
than starting from scratch, so the parts the user was happy with survive. The
flow runs one direction only: `.excalidraw` is the source, the SVG is derived.
If the user edits the diagram at excalidraw.com, they save the scene back to
`study/diagrams/` and re-export - never the other way round.
