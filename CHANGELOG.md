# GPT4FreeCAD Changelog

## [2.7.0] - 2026-08-06

Stops blaming the user for the protocol's own mistake, and gives OpenRouter
real schema enforcement.

### Fixed
- **Reusing an object name no longer fails the step.** Engineering mode appends,
  so asking to change something that already exists makes the model define that
  name again — "a hole bored in the centre", then "bore the hole through",
  and the second step died on *object name 'hole' is already used*. The
  collision is created by the append-only protocol, not by the user, and the
  correction never needs judgement, so it is now applied deterministically:
  `hole` becomes `hole_2` with a note in the log, no repair round spent.
  References are rewritten only when they point at a name defined in the same
  batch, so an op named `hole` whose target is `hole` correctly becomes
  `hole_2` cut from the original.
- **A hole that misses the material is caught.** The boolean check only asked
  whether *anything* had been removed, so a 6 mm hole positioned on the bottom
  face and drilled downwards took **0.28 mm³ out of 6283** and reported success
  — which is what made the follow-up step necessary in the first place. Holes
  are now measured against the volume the drill itself sweeps, and one that
  removes less than a fifth of it fails with an explanation of what `position`
  means. Threshold deliberately slack: a hole at the edge of a part or breaking
  into a cavity legitimately removes less than its own volume.
- **Optional fields sent as `null` no longer break the build.** Strict
  structured outputs have no optional properties — every one must be present —
  so a model using them writes `"axis": null` where a plain JSON reply omits
  the key. `op.get("axis", default)` then returned None and `Vector(*None)`
  ended the build. Absent and null now mean the same thing throughout the
  interpreter and the validator, including inside `placement`. Found by running
  a strict-shaped reply through a real build, not by inspection.

### Added
- **OpenRouter enforces the schema.** `schema.json_schema(strict=True)` emits
  the structured-outputs dialect (every object closed, every property required,
  optional properties nullable, no size keywords) and the adapter sends it as
  `response_format: json_schema`. 263 of OpenRouter's 340 models advertise
  support; for the rest the first request downgrades to plain JSON mode and the
  model is remembered for the session, so the discovery costs one request, once.
  An auth or network failure is never mistaken for a schema rejection.
  The permissive schema is untouched — the local grammar path still needs it.
- **xAI (Grok) provider.** OpenAI-compatible endpoint at `api.x.ai` with its own
  key, live catalogue from `/v1/models`, and the same searchable picker. Sends
  the strict schema too. `default_models` is deliberately thin: model names
  there change often, and Browse… is the answer rather than a literal that rots.

### Changed
- 206 unit tests (was 181).

## [2.6.0] - 2026-08-05

Model lists stop being a Python literal.

Every provider carried a hard-coded `default_models` list, so a model released
after the last commit was invisible until somebody edited the source. That is
the wrong shape for a thing that changes weekly. Providers are now asked what
they have.

### Added
- **Live model catalogues.** `Provider.fetch_models()` queries the provider's
  own model endpoint; `default_models` is demoted to the offline fallback it
  always should have been. Implemented for OpenAI (`/v1/models`, filtered to
  models that can hold a conversation and sorted newest first), Anthropic
  (`/v1/models`), Gemini (`/v1beta/models`, keeping only models that support
  `generateContent`) and OpenRouter.
- **A searchable model picker** (`ui/model_picker.py`). **Browse…** beside any
  Model box fetches the catalogue on a worker thread and opens a filterable
  table: id, context length, price per million in/out, and whether the model
  accepts a JSON response. Space-separated filter words all have to match, so
  "qwen coder" finds `qwen/qwen3-coder` without an exact prefix. Free-only and
  JSON-capable toggles; the latter is on by default, because structured mode
  asks every model for JSON and one that cannot be asked will fail every
  generation. The combo stays editable for ids newer than the catalogue.
- **OpenRouter provider.** One key for the whole ecosystem — 340 models when
  this was written, most of them released after any list this addon could ship
  with. The catalogue endpoint is public, so the picker works before a key is
  pasted. Sends the documented attribution headers, and translates OpenRouter's
  habit of reporting routing failures as HTTP 200 with an `error` member into a
  real error instead of a confusing "no choices".
- **Ollama / LM Studio provider** (`llm/localserver.py`). Attaches to a local
  OpenAI-compatible server you are already running, so anything pulled in those
  apps is usable without this addon managing weights, ports or processes.
  **Detect** probes the usual ports (Ollama 11434, LM Studio 1234, Jan 1337).
  URLs are normalised, so pasting `:11434`, `/v1` or the full chat-completions
  path all work. Distinct from the existing `machine` provider, which downloads
  and supervises a llama-server itself.
- **"Find on this PC…"** (`llm/discovery.py`). Lists GGUF models already
  downloaded by LM Studio, GPT4All, Hugging Face or llama.cpp rather than
  asking you to remember where that app put them. Skips what a `.gguf` walk
  otherwise turns up and cannot chat: `mmproj-*` multimodal projectors,
  embedding and reranker models, files under 100 MB, and every shard of a split
  model but the first. Ollama is deliberately not scanned — it stores weights as
  extensionless blobs, and its models are better reached through the provider
  above.

### Fixed
- The Test button called the local provider's activation report for *any*
  key-less provider, which would have failed with an AttributeError once a
  second key-less provider existed. It now dispatches on the provider, and
  key-less providers get a real chat round-trip.
- The panel's model dropdown was populated only from `default_models`, so a
  provider whose models all come from a live catalogue would show an empty box
  even with a model configured. A saved model is now always present.

### Changed
- Provider registration order is now the display order, with Gemini first to
  match the default.
- 181 unit tests (was 159), covering catalogue parsing for all four cloud
  providers from trimmed real fixtures, local-server URL normalisation and
  error messages, and the discovery filters.

## [2.5.0] - 2026-08-05

Catches the failures that never looked like failures.

The repair loop only ever fired on something that announced itself: a rejected
plan, a build error, an inspection warning. Probing 26 schema-valid programs
against FreeCAD 1.1 found that the more common bad outcome announces nothing.
Eight of them built successfully, passed inspection as clean, and produced
geometry that was not what was asked for.

### Fixed
- **`_check_built` tested only for a null shape.** OCC also returns shapes that
  exist and are wrong. A `fillet` of radius 50 on a 10 mm box yielded a
  self-intersecting solid of **volume −21025 mm³ with a 932×990×397 mm bounding
  box**, which was not null, so it was committed as the finished part. The check
  now tests validity and positive volume, and runs against *every* object rather
  than the last one — so the error names the operation that caused it. This alone
  catches the collinear profile, the bowtie, the zero-radii cone, the on-axis
  revolve, and the empty `common`, each at its own operation.
- **The fillet/chamfer retry accepted the first non-null shape**, which is how
  that −21025 mm³ result got through. It now requires a sound shape, and shrinks
  through six halvings instead of four: the radius-50 case now builds a clean
  789 mm³ solid with a note, where before it produced garbage.
- **`shell` silently did nothing** when the wall was too thick for the part.
  A thickness of 25 mm on a 10 mm wall returned the original solid unchanged
  (16000 mm³, the full volume) and the program carried on believing it had
  hollowed the part. It is now an error that says so.
- **Post-build inspection looked at one object.** A program that builds a plate,
  builds a rib it never references, and fillets the plate finished with a healthy
  filleted plate and a loose rib in the document that nothing ever examined.
  `schema.leaf_names()` finds every end product and all of them are now measured.

### Added
- **Semantic validation for the values OCC accepts and quietly rewrites.**
  Measured, not assumed: a `cylinder` angle of 0, −30 or 400 all build a full
  360° cylinder; a `revolve` of 400° builds 40°; a `cone` with radius1 −5 builds
  one with radius1 0; a profile with a repeated point drops a corner (a
  four-point square built a triangle, 250 mm³ instead of 500). Also rejects
  self-intersecting and collinear profiles, and a `torus` whose tube is thicker
  than its ring. Each error explains what FreeCAD would have done instead.
  Repeating the first point to close a profile stays legal — OCC tolerates it.
- **A self-review round.** When the geometry is sound but a measurement
  disagrees with the request — the largest length the user stated appears
  nowhere in the built part — the model is shown the request, the measurements
  and the program, and may either return a correction or sign the build off
  unchanged. Only the largest stated dimension is checked, and only for a
  single-part result: a description mixes overall sizes with hole diameters and
  wall thicknesses, and only the largest is reliably an overall dimension. A
  clean build that matches the request costs no extra request.
- **Repair prompts now carry the measurements.** A round told only "the result
  has zero volume" had to guess which operation was at fault; it can now see
  that the tool it cut with sits 200 mm from the part it was cutting.
- **The system prompt states the geometry rules**, including the one that was
  never written down: every object you create must be used by a later operation
  or be the final result.

### Changed
- `interpreter.build_program()` returns `(result, objects, log)`, matching
  `rebuild()`. Callers need the object map to inspect every end product.
- 159 unit tests (was 113), including a new `tests/test_panel_flow.py` covering
  the panel's build → inspect → repair → review decisions — the wiring between
  the deterministic checks and the model round-trips, which had no tests at all.
  It binds the real methods to a stand-in object, so it needs no Qt widget and
  no FreeCAD, only the Qt binding FreeCAD ships; run it with `freecadcmd`.
- Verified against FreeCAD 1.1 headless: all six built-in templates plus six
  harder programs (revolve, mirror, shell, chamfer, rotated placement, patterns,
  partial cylinder, concave extrude) build clean with zero regressions.

## [2.4.2] - 2026-07-30

Local models get the output guarantee on every server, not just one.

### Changed
- **Structured mode is now grammar-constrained on any local server.** The addon
  compiles the CAD schema to a GBNF grammar itself (`llm/gbnf.py`, vendored
  from the Machine Activation SDK, standard library only) and sends a ready
  grammar, which llama.cpp enforces in its sampler. Previously only
  `machine serve` got enforcement: `supports_schema()` probed for it and
  **disabled constraints entirely** on a bare `llama-server` — which is the
  server this addon starts for you, so the common case ran unconstrained and
  leaned on auto-repair.
  - That probe was written on the belief that a bare server's own
    schema-to-grammar conversion was pathologically slow (reportedly 400 s+ for
    the full schema). Re-measured against the same llama.cpp build
    (`b10182-afeebe103`), it is not: ~190 ms of per-request setup. The
    workaround was unnecessary, and skipping enforcement cost far more than it
    saved. It is gone.
  - Verified end to end: a bare `llama-server` running a 1.7 B model returned a
    valid four-operation program (box → cylinder → cut → fillet) that passes
    the validator on the first attempt.
  - The grammar is compiled once per schema and cached; the two
    `response_format` spellings remain as a fallback for a server that ignores
    `grammar`.
- 113 unit tests (was 111).

## [2.4.1] - 2026-07-30

A lathe, and a hardening pass over the last two releases.

### Added
- **`revolve` operation.** Spin a closed `[r, z]` profile around the Z axis —
  `r` is the distance from the axis, `z` the height — with an optional `angle`
  for a partial revolve. This was the IR's biggest expressiveness gap: flanges,
  shafts, pulleys, bottles and vases previously had to be approximated with a
  stack of boolean primitives. Points behind the axis (`r < 0`) are rejected
  with an explanation rather than producing a self-intersecting solid.
- **Unload button for the local model** (Settings → Local), freeing its memory
  without restarting FreeCAD.

### Fixed
- **The Python-mode deny-list was bypassed on every auto-run.** `prechecked=True`
  was passed unconditionally, so with *Build automatically* on (the default)
  model-generated Python ran unreviewed with the safety check disabled. It is
  now skipped only for an explicit **Build** click, where the user has seen and
  can edit the code.
- **A launched local model outlived FreeCAD.** Neither `deactivate_model()` nor
  `backend.stop()` had a caller, and on Windows a child process survives its
  parent — closing FreeCAD stranded a multi-gigabyte `llama-server`. The
  backend now registers an `atexit` hook when it starts one.
- **"Test connection" failed on reasoning models.** It asked for 16 output
  tokens; on models that think before answering (Claude 5, GPT-5, Gemini 3)
  thinking spends the same budget, so a valid key reported an empty reply.
  Raised to 512.
- **Claude 5 plans arrived truncated.** Thinking shares `max_tokens` with the
  visible reply, so the 4096 default cut complex programs off mid-plan. The
  adapter now floors thinking-by-default models at 16384, mirroring what the
  Gemini adapter already did for Gemini 3.
- **The OpenAI adapter 400'd on current models.** gpt-5.x and the o-series
  require `max_completion_tokens` and reject a custom `temperature`; the
  adapter now sends the right shape per model family, applies a reasoning
  budget floor, and its default model list leads with GPT-5 instead of GPT-4o.
- **Testing a local model froze the whole application.** Activation ran on the
  GUI thread and can take up to 900 s to load weights; it now runs on a worker
  thread behind a progress dialog.
- **Repair rounds asked the model to fix a plan it could not see.** A reply that
  fails validation never enters the conversation history, so rounds 2+ received
  only an error message. The rejected reply is now attached to the error and
  echoed back (truncated) in the repair prompt.
- Gemini 3 requests no longer share the 120 s timeout with non-thinking models
  (raised to 300 s, matching Anthropic).
- The conversation history is capped at 24 messages, so a long session cannot
  grow the prompt — and its cost — without bound.

### Changed
- Settings no longer shows a second, empty **Model** row for the local provider;
  its model is the `.gguf` file picked above.
- Removed ~215 lines of dead code left over from the compact-UI rewrite
  (`_build_legacy_ui` in the panel and the engineering widget, the unused shape
  hints, and the `structured_system_prompt` shim).
- 111 unit tests (was 104).

## [2.4.0] - 2026-07-29

Run it on your own machine, and stop hand-holding failed builds.

### Added
- **Local models — a fourth provider, no API key and no cloud.** *Local (Machine
  Activation)* runs a GGUF model on your own hardware via the
  [Machine Activation SDK](https://github.com/revhappy/MachineActivationSDK)
  (MIT, `llama.cpp` under the hood). Offline, free per part, and nothing you
  design leaves the computer. Start a server with
  `machine serve <model.gguf>`, then point Settings at it (default
  `http://127.0.0.1:8177`).
  - **Structured output is grammar-enforced.** The CAD program schema
    (`schema.json_schema()`, previously unused) is compiled to a GBNF grammar
    and enforced inside llama.cpp's sampler, so every operation is structurally
    valid by construction. Asking a 4B model nicely for JSON fails constantly;
    this cannot — which is what makes small local models usable for CAD at all.
    Cross-operation rules (references, unique names, positive dimensions) remain
    the validator's job, backed by auto-repair.
  - Verified end to end on `gemma-4-E4B-it` (IQ4_NL, CPU): a plate-with-a-hole
    and a filleted cube each produced a valid program on the first attempt.
  - **Test connection reports the activation contract** — whether the model
    actually fits this machine, which acceleration it got (CPU/GPU/NPU), and
    what is degraded. No cloud API has an equivalent.
  - Works with or without `pip install machine-activation`: the SDK's
    dependency-free Python client is used when importable (it also supplies the
    activation report), otherwise the same HTTP is spoken with the standard
    library, so the provider works in FreeCAD's bundled Python.
  - Local traffic bypasses the system proxy — otherwise `urllib` routes
    `127.0.0.1` through `$http_proxy`, which breaks on a corporate machine and
    would send prompts through a host you never chose.
- **Auto-repair harness** (`harness.py`) — a repair budget per user action,
  configurable in Settings (**Auto-repair rounds**, default 3, `0` disables).
  Previously every failure path was capped at a single attempt.
  - A **loop guard** fingerprints plans that already failed and stops the loop
    when the model returns the same broken program instead of spending the rest
    of the budget on it.
  - **Python mode is now repaired too** (it previously got no retry at all),
    with the traceback pinned to the failing line of the generated script.
  - **Engineering timeline steps are repaired** — a step that fails to build is
    rolled back and retried with the error, instead of only logging
    "Rebuild failed - edit the step".
- Transient provider failures (429 / 5xx / network) are retried with backoff, so
  a rate-limit blip no longer kills a generation mid-repair.
- 14 new unit tests (79 total).

### Fixed (local models)
- **`json_schema()` now carries each operation's required fields.** It only
  constrained `op` itself, so a grammar built from it permitted `{"op": "box"}`
  with no name or dimensions — accepted by the sampler, then rejected by
  `validate_program`. Caught by running a real local model. The schema is now
  generated per-operation from `OPERATIONS` (required fields, types, enums), so
  the grammar enforces what the validator checks and the two cannot drift.

### Changed
- **Build errors name the operation that failed** and list what had already been
  built — `operation #4 'fillet' ('rounded') failed: … [objects built so far:
  'base', 'boss']` — rather than a bare OpenCascade message. Far more of these
  are fixed on the first retry.
- `Provider.requires_key` is now honoured: keyless providers no longer trip the
  "No API key" gate, and Settings shows a server URL instead of a key field.

### Fixed
- **Oversized fillets/chamfers no longer fail the build.** The value is halved
  until the geometry kernel accepts it (logged when it happens), and
  out-of-range edge indices are dropped instead of aborting — deterministic
  fixes that cost milliseconds rather than an LLM round-trip.
- **A boolean that removes no material is now caught.** A `cut` or `hole` whose
  tool misses its target used to "succeed" and leave the part untouched; a
  volume comparison now reports it and feeds it into auto-repair.

## [2.3.1] - 2026-07-29

Fixes found by dogfooding the engineering timeline with Claude as the model.

### Fixed
- **Anthropic provider works again on current Claude models.** The adapter
  forced JSON output by prefilling an assistant turn with `{` and always sent
  `temperature` - both are rejected with HTTP 400 by Claude 4.6+/5 models
  (including the former default `claude-sonnet-4-6`), so every request with a
  modern Claude model failed. JSON is now enforced by the prompt alone (the
  tolerant extractor and auto-repair round already handle the rest), and
  sampling parameters are only sent to models that accept them.
- **Fillet/chamfer "all edges" no longer fails on round parts.** Cylindrical
  and conical faces carry a seam edge that OpenCascade cannot fillet or
  chamfer; including it made `Part::Chamfer`/`Part::Fillet` silently compute a
  null shape (e.g. chamfering a plain washer). Seam and degenerate edges are
  now excluded when no explicit edge list is given.
- **Null geometry now fails the build instead of committing silently.** A
  parametric feature that fails to compute on recompute (e.g. an oversized
  chamfer) previously left a "no geometry" step in the engineering timeline;
  the rebuild now raises a clear error and the transaction rolls back.

### Added
- Anthropic: Claude 5 model list (`claude-opus-5` default, `claude-fable-5`),
  a clear error when safety classifiers decline a request, an automatic
  server-side fallback for declined requests on Claude 5-tier models, and a
  5-minute request timeout floor for thinking models.
- API keys fall back to standard environment variables (`ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY`, `OPENAI_API_KEY`) when not set in Settings.
- 3 new unit tests covering the Claude 5 request shape and refusal handling
  (65 total, replacing the obsolete prefill test).

## [2.3.0] - 2026-07-29

Start from a part, not a blank prompt.

### Added
- **Template library** (`cad/templates.py`) — a **Template** picker with six built-in
  parametric starter programs: circular flange (bolt circle + centre bore), L-bracket,
  open-top electronics enclosure (with corner screw bosses), gear blank, mounting plate,
  and spacer/standoff. Selecting one pre-fills the editable plan preview (Structured) or
  seeds the engineering timeline (Engineering) with clean named features — **no API
  call**. In Structured mode the plan is also seeded into the conversation, so follow-ups
  like *"make the holes M5"* refine the template.
- **Save plan as template** — a Save button next to the picker stores the current plan
  (preview or timeline) as a user template: a plain `{"operations": [...]}` JSON file in
  the user templates folder (`GPT4FreeCAD/templates` under the FreeCAD user directory;
  override with `GPT4FREECAD_TEMPLATES`). User templates appear in the picker immediately
  and in every later session; unreadable files are reported, not fatal.
- 6 new unit tests (63 total).

### Changed
- The **Shape hint** dropdown is replaced by the Template picker (as sketched in
  IDEAS.md); the picker is available in both Structured and Engineering modes.

## [2.2.0] - 2026-07-29

The model now checks its own work. Ideas adapted from the open-source
[CAD Skills](https://github.com/earthtojake/text-to-cad) agent framework.

### Added
- **Post-build geometry inspection** (`cad/inspect.py`) — after every build the result is
  inspected: kernel validity, solid count, watertightness, volume, and bounding box, with a
  one-line report in the Activity log. In Structured mode a defective build (e.g. a boolean
  that missed its target, a zero-volume result, an open shell) is **undone automatically**
  and the inspection report is sent back to the model for one corrected plan — extending the
  existing schema-repair retry into a full geometry repair loop. Other modes log warnings.
- **STEP export** — a new STEP button next to STL exports the exact B-rep geometry
  (`Part.export`) for CAD/CAM interchange. Enabled after any successful build.
- **Engineering defaults in the prompt** — standard values applied when the user doesn't
  specify: M3/M4/M5 clearance holes (3.4/4.5/5.5 mm) and counterbores (6.5/8.0/10.0 mm),
  2.0–3.0 mm walls, 1.0–3.0 mm cosmetic fillets, part seated flat on XY, closed
  positive-volume solids. Included in both Structured and Engineering modes.
- 12 new unit tests (57 total).

## [2.1.0] - 2026-05-29

Turns GPT4FreeCAD into a precision engineering tool, not just a generator.

### Added
- **Engineering mode (step-by-step feature timeline)** — build/edit a part as an ordered list
  of operations. Add steps via AI (`+ AI step`, generated one increment at a time with full
  context of the existing program) or by hand (`+ Manual`, a precise schema-driven parameter
  form). Select a step to edit its exact parameters, reorder/insert/delete steps, and the whole
  tree **rebuilds deterministically**. The model is given extra engineering discipline
  (datums, manufacturable dimensions, one feature per step, finishing last).
- **3D-print mode** — a toggle that composes with Structured and Engineering modes:
  printability rules + build-volume constraint added to the prompt (default 254 mm = 10 in per
  axis, editable in Settings), a post-build bounding-box check that offers one-click
  **Scale to fit**, and **Export STL…** with configurable mesh deviation.
- **Five new operations**: `linear_pattern`, `polar_pattern`, `mirror`, `shell` (hollow), and
  `hole` (with optional counterbore / countersink).
- New schema field kinds (`int`, `bool`, `enum`) and a `validate_op` helper powering instant
  per-field validation in the engineering form.
- `engine.generate_step`, `interpreter.rebuild`, and `cad/export.py` (STL export + a pure
  `overage` helper). New UI modules `ui/engineering.py` and `ui/op_form.py`.

### Changed
- Gemini model list is now the **Gemini 3 family only** (`gemini-3.5-flash`,
  `gemini-3-flash-preview`, `gemini-3.1-pro-preview`); 1.x/2.0/2.5 removed.
- The "Mode" selector is now Structured / Engineering / Python.

## [2.0.0] - 2026-05-29

A ground-up rewrite turning GPT4FreeCAD from a single-provider proof-of-concept into a
usable, structured CAD assistant.

### Added
- **Multi-provider LLM layer** — Google Gemini, OpenAI, and Anthropic Claude, behind a
  pluggable provider registry. Each has its own API-key field and editable model list.
- **Gemini 3 support** — defaults to the Gemini 3 family (`gemini-3.5-flash`,
  `gemini-3-flash-preview`, `gemini-3.1-pro-preview`) plus 2.5 Flash/Pro. Per Google's
  guidance, temperature is left at the default 1.0 for Gemini 3 and a generous output
  budget is reserved for "thinking". A **Thinking level** selector (minimal/low/medium/high,
  or default) exposes Gemini 3's reasoning depth; "minimal" is auto-bumped to "low" for Pro,
  which does not support it.
- **Structured outputs (the headline feature)** — the model emits a validated JSON
  *intermediate representation* of CAD operations (`box`, `cylinder`, `sphere`, `cone`,
  `torus`, `extrude`, `cut`, `fuse`, `common`, `fillet`, `chamfer`, `translate`, `rotate`).
- **Deterministic interpreter** — builds the IR into native **parametric** FreeCAD objects
  (an editable feature tree), inside a single undo transaction.
- **Automatic repair retry** — invalid programs are sent back to the model with the precise
  validation error for one self-correction attempt; build failures trigger a fix as well.
- **Structured inputs** — provider/model/mode/units selectors and a shape hint, plus an
  editable plan preview you can tweak before building.
- **Workbench + dockable panel** — a proper FreeCAD workbench with a toolbar, menu, and a
  side panel, replacing the old modal dialog.
- **Settings dialog** — manage keys, per-provider models, temperature, max tokens,
  auto-build, and an OpenAI-compatible endpoint override (Azure/OpenRouter/local). Includes
  a "Test connection" button.
- **Advanced Python mode** — opt-in code-generation path with a safety denylist.
- **Background worker** — LLM calls run off the UI thread so FreeCAD stays responsive.
- **PySide2/PySide6 compatibility shim** — works on FreeCAD 0.20/0.21 and 1.0+.
- **Unit tests** — 28 tests covering schema validation, JSON extraction, all three
  providers (network-mocked), the engine repair loop, and config persistence.

### Changed
- API keys now live in FreeCAD preferences (password-masked), not a plaintext
  `~/api_key.txt`. The legacy file is migrated automatically on first run.
- **Zero third-party dependencies** — replaced `requests` with the standard library.
- Modernised `package.xml` to the FreeCAD package-metadata format with a workbench entry.

### Removed
- Legacy `gpt.py`, `gpt4_integration.py`, and the misnamed `_init_.py` (superseded by the
  `gpt4freecad` package).

### Security
- Generated code is no longer `exec()`'d by default; the safe structured path is the
  default. Python mode is opt-in and screened by a denylist.

## [1.0.0] - 2023-05-02

### Added
- Initial release of GPT4FreeCAD.
- GPT-4 integration for generating Python scripts based on user input.
- Basic UI for user input and execution of generated code.
- Undo functionality.
- Conversation history.
