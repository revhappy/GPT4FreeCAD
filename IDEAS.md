# Future ideas / roadmap

Backlog of ideas worth building, mostly harvested from the open-source
[CAD Skills](https://github.com/earthtojake/text-to-cad) agent framework (formerly
*text-to-cad*, 11.8k★) plus our own. Implemented in 2.2.0: STEP export, prompt
engineering defaults, post-build geometry inspection + repair loop. Implemented in
2.3.0: template library of common setups (six built-in starters + "Save plan as
template", replacing the Shape hint with a Template picker). Everything below is
**not yet implemented**, roughly in priority order.

## 1. `revolve` op (then `sweep` / `loft` / `helix`)
The IR's biggest expressiveness gap. `revolve` (spin a closed 2D profile around an
axis) unlocks flanges, shafts, pulleys, vases — huge class of parts. FreeCAD:
`Part::Revolution` or `face.revolve()`. Thanks to the table-driven schema, each new op
is one `schema.OPERATIONS` entry + one interpreter handler (prompt + form derive
themselves). `sweep`/`loft` later enable impeller blades; `helix` enables threads and
spiral staircases.

## 2. Benchmark / eval suite (CAD Skills' 10 parts)
A regression harness that runs reference prompts through each provider and asserts
geometric facts (bbox, volume within tolerance, solid count) using `cad/inspect.py`.
CAD Skills' graded difficulty ladder: calibration block → circular flange → L-bracket
→ stepped shaft with keyway → open-top electronics enclosure → clevis bracket with
lightening cutouts → radial engine cylinder → centrifugal impeller → spiral staircase
→ planetary gear stage. Needs FreeCAD headless (`freecadcmd`) to build; also serves as
a gap-finder for missing ops (several of those parts need revolve/sweep/helix).

## 3. Visual feedback loop + image input
CAD Skills mandates a snapshot after every build so the agent can *see* its work.
- **Snapshot self-check**: after a build, capture the viewport
  (`Gui.ActiveDocument.ActiveView.saveImage()`) and optionally send it back to the
  model ("does this match the description?") for a refinement pass. All three
  providers (Gemini, OpenAI, Claude) are multimodal.
- **Image input**: accept a reference photo/sketch/drawing alongside the text
  description. Requires provider payload changes (image parts) + a UI attach button.

## 4. Deeper inspection
- Inspect **each component** in Separate layout (currently only the final result
  object is inspected), e.g. walk the `GPT4FreeCAD_Assembly` group.
- **Dimension verification**: extract user-stated dimensions from the request and
  check them against the built bounding box / feature sizes (CAD Skills workflow
  step 8: "verify user-specified dimensions").
- Interference/clearance checks between separate components.

## 5. More export formats
3MF (colour/multi-material printing, `Mesh.export`) and GLB/glTF (web preview,
FreeCAD 1.0 exporter) alongside STL/STEP. CAD Skills treats STEP as primary and
STL/3MF/GLB as secondary — we now match the STEP part.

## 6. Plan-then-generate ("CAD brief")
CAD Skills' workflow writes a brief (dimensions, features, datums, validation
targets) before modeling. Cheap version: allow an optional `"plan"` string field in
the JSON program that the model fills first (chain-of-thought for structured output)
and the interpreter ignores; or a two-call flow for complex parts.

## 7. Standard parts knowledge
Expand the prompt's hardware table (tap-drill sizes, hex-nut pockets, heat-set insert
bores, bearing seats 608/625/6800…). Full CAD-Skills-style off-the-shelf STEP part
sourcing (step.parts) is heavyweight; a richer prompt table gets most of the value.

## Explicitly not worth taking (evaluated 2026-07-29)
- **build123d** as the geometry layer — redundant with FreeCAD's kernel, would
  reintroduce pip dependencies (we're stdlib-only by design).
- **WebGL viewer** — FreeCAD *is* our viewer.
- **URDF/SRDF/SDF, G-code slicing, SendCutSend/Bambu integrations** — different
  product scope.
