# Future ideas / roadmap

Backlog of ideas worth building, mostly harvested from the open-source
[CAD Skills](https://github.com/earthtojake/text-to-cad) agent framework (formerly
*text-to-cad*, 11.8k★) plus our own. Implemented in 2.2.0: STEP export, prompt
engineering defaults, post-build geometry inspection + repair loop. Implemented in
2.3.0: template library of common setups (six built-in starters + "Save plan as
template", replacing the Shape hint with a Template picker). Implemented in 2.4.1:
the `revolve` op. Everything below is **not yet implemented**, roughly in priority
order.

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

## 4. Visual feedback loop + image input
CAD Skills mandates a snapshot after every build so the agent can *see* its work.
- **Snapshot self-check**: after a build, capture the viewport
  (`Gui.ActiveDocument.ActiveView.saveImage()`) and optionally send it back to the
  model ("does this match the description?") for a refinement pass. All three
  providers (Gemini, OpenAI, Claude) are multimodal.
- **Image input**: accept a reference photo/sketch/drawing alongside the text
  description. Requires provider payload changes (image parts) + a UI attach button.

## 5. Deeper inspection
- Inspect **each component** in Separate layout (currently only the final result
  object is inspected), e.g. walk the `GPT4FreeCAD_Assembly` group.
- **Dimension verification**: extract user-stated dimensions from the request and
  check them against the built bounding box / feature sizes (CAD Skills workflow
  step 8: "verify user-specified dimensions").
- Interference/clearance checks between separate components.

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
