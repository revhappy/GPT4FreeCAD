# Future ideas / roadmap

Backlog of ideas worth building, mostly harvested from the open-source
[CAD Skills](https://github.com/earthtojake/text-to-cad) agent framework (formerly
*text-to-cad*, 11.8k★) plus our own. Implemented in 2.2.0: STEP export, prompt
engineering defaults, post-build geometry inspection + repair loop. Implemented in
2.3.0: template library of common setups (six built-in starters + "Save plan as
template", replacing the Shape hint with a Template picker). Implemented in 2.4.1:
the `revolve` op. Implemented in 2.5.0: whole-program inspection (every end
product, not just the last object), semantic validation of the values OCC
silently rewrites, and the warning-triggered self-review round. Everything below
is **not yet implemented**, roughly in priority order.

## 1. `sweep` / `loft` / `helix`
`revolve` (2.4.1) closed the biggest expressiveness gap; these are the rest of it.
`sweep`/`loft` enable impeller blades and transitions; `helix` enables threads and
spiral staircases. Thanks to the table-driven schema, each new op is one
`schema.OPERATIONS` entry + one interpreter handler (prompt + form derive themselves).

## 2. Benchmark / eval suite (CAD Skills' 10 parts)
A regression harness that runs reference prompts through each provider and asserts
geometric facts (bbox, volume within tolerance, solid count) using `cad/inspect.py`.
CAD Skills' graded difficulty ladder: calibration block → circular flange → L-bracket
→ stepped shaft with keyway → open-top electronics enclosure → clevis bracket with
lightening cutouts → radial engine cylinder → centrifugal impeller → spiral staircase
→ planetary gear stage. Needs FreeCAD headless (`freecadcmd`) to build; also serves as
a gap-finder for missing ops (the remaining ones need sweep/loft/helix).

## 3. Structured outputs for the cloud providers
`ChatRequest` already carries `json_schema`, but only the local provider uses it (as a
GBNF grammar) — the three cloud adapters ignore it and ask for JSON in prose. All three
can enforce it now: Anthropic `output_config.format`, OpenAI `response_format:
{type: "json_schema", strict: true}`, Gemini `responseSchema`. Needs a sanitised schema
variant (Anthropic/OpenAI strict mode requires `additionalProperties: false` and rejects
`minLength`/`minItems`), so `json_schema()` would grow a `strict=True` flavour. Payoff is
the same one grammar enforcement gave local models: whole classes of repair rounds stop
happening.

2.6.0 raised the value of this: OpenRouter is now a provider, and **263 of its 340
models advertise `structured_outputs`** (286 advertise `response_format`). Its adapter
sends `{"type": "json_object"}` today and carries a note pointing here — the sanitised
schema variant is the one thing standing between the addon and schema enforcement across
almost the entire open-weight ecosystem, not just three cloud vendors. The picker already
records the capability per model (`ModelInfo.json_mode`), so the plumbing to *choose* an
enforcing model exists.

## 4. Visual feedback loop + image input
CAD Skills mandates a snapshot after every build so the agent can *see* its work.
- **Snapshot self-check**: after a build, capture the viewport
  (`Gui.ActiveDocument.ActiveView.saveImage()`) and optionally send it back to the
  model ("does this match the description?") for a refinement pass. All three
  providers (Gemini, OpenAI, Claude) are multimodal.
- **Image input**: accept a reference photo/sketch/drawing alongside the text
  description. Requires provider payload changes (image parts) + a UI attach button.

## 5. Deeper inspection
2.5.0 took the first two items: every end product is inspected (`schema.leaf_names()`
+ `inspect.inspect_leaves()`), and the largest user-stated dimension is checked against
the built bounding box. What is left:
- **Interference/clearance checks** between separate components — the natural next
  step now that each component is measured individually.
- **Feature-level dimension verification**: the current check is deliberately
  conservative (largest stated length, single-part results only), because a
  description mixes overall sizes with hole diameters and wall thicknesses and
  nothing distinguishes them. Matching stated dimensions to *specific features*
  (this 5 mm is a hole, that 60 mm is a bolt circle) needs the model's help, and
  is what would let the check cover every number in the request.
- **Overlapping patterns**: `linear_pattern` with spacing smaller than the source
  fuses its copies into one blob (5 copies of a 1000 mm³ box measured 1400 mm³, not
  5000). Legitimate for some designs, so it needs intent, not a hard rule.

## 6. More export formats
3MF (colour/multi-material printing, `Mesh.export`) and GLB/glTF (web preview,
FreeCAD 1.0 exporter) alongside STL/STEP. CAD Skills treats STEP as primary and
STL/3MF/GLB as secondary — we now match the STEP part.

## 7. Plan-then-generate ("CAD brief")
CAD Skills' workflow writes a brief (dimensions, features, datums, validation
targets) before modeling. Cheap version: allow an optional `"plan"` string field in
the JSON program that the model fills first (chain-of-thought for structured output)
and the interpreter ignores; or a two-call flow for complex parts.

## 8. Standard parts knowledge
Expand the prompt's hardware table (tap-drill sizes, hex-nut pockets, heat-set insert
bores, bearing seats 608/625/6800…). Full CAD-Skills-style off-the-shelf STEP part
sourcing (step.parts) is heavyweight; a richer prompt table gets most of the value.

## Explicitly not worth taking (evaluated 2026-07-29)
- **build123d** as the geometry layer — redundant with FreeCAD's kernel, would
  reintroduce pip dependencies (we're stdlib-only by design).
- **WebGL viewer** — FreeCAD *is* our viewer.
- **URDF/SRDF/SDF, G-code slicing, SendCutSend/Bambu integrations** — different
  product scope.
