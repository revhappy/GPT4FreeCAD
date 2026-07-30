# GPT4FreeCAD Changelog

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
