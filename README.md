# GPT4FreeCAD

Generate **parametric** FreeCAD geometry from plain English — powered by your choice of
**Google Gemini, OpenAI, Anthropic Claude, xAI Grok, OpenRouter's several hundred models,
or a local model on your own machine**.

![Workbench Logo](logo.svg)

> **v2 rewrite.** GPT4FreeCAD is no longer a toy that blindly `exec()`s whatever the
> model returns. The model now emits a **validated, structured CAD program** (JSON) that a
> deterministic interpreter turns into a real, editable feature tree. An optional advanced
> Python mode is still available for power users.
>
> **New in 2.1 — an engineering tool, not just a generator:**
> - **Engineering mode** — build a part as an editable *feature timeline*: add steps (AI or by
>   hand), tune each step's exact parameters in a structured form, reorder/insert/delete, and
>   deterministically rebuild. Like a parametric macro you author one step at a time.
> - **3D-print mode** — a toggle that biases the model toward printability, constrains the part
>   to your printer's build volume, warns + offers *scale-to-fit* if it's too big, and adds
>   one-click **STL export**.
> - **More operations** — `linear_pattern`, `polar_pattern`, `mirror`, `shell` (hollow), `hole`
>   (counterbore/countersink).
> - **Fused / Separate layout** - keep the normal single-solid workflow, or generate independent
>   manufacturing components grouped in an editable FreeCAD assembly container.
> - **Gemini 3** family models by default.
>
> **New in 2.2 — the model checks its own work:**
> - **Post-build geometry inspection** — every build is verified (valid solid, watertight,
>   non-zero volume, connected) and logged. In Structured mode a defective result is undone
>   automatically and the inspection report is sent back to the model for a corrected plan.
> - **STEP export** — one-click exact-geometry export alongside STL, for CAD/CAM interchange.
> - **Built-in engineering defaults** — standard clearance-hole sizes (M3/M4/M5), wall
>   thicknesses, and fillet conventions applied when your description leaves them out.
>
> **New in 2.3 — start from a part, not a blank prompt:**
> - **Template library** — a **Template** picker with six built-in parametric starters
>   (flange, L-bracket, open-top enclosure, gear blank, mounting plate, spacer/standoff).
>   Selecting one fills the plan preview or seeds the engineering timeline with clean named
>   features — no API call — then tweak parameters or ask the AI to modify it.
> - **Save plan as template** — store any plan or timeline as a reusable user template
>   (a plain JSON file); it shows up in the picker across sessions.
>
> **New in 2.4 — run it entirely on your own machine, and let it fix itself:**
> - **🔒 Local models, no API key, no cloud.** A fourth provider runs a GGUF model on your
>   own hardware through the [Machine Activation SDK](https://github.com/revhappy/MachineActivationSDK)
>   — offline, free per-part, and nothing you design ever leaves the computer. Structured mode
>   gets a guarantee no cloud provider offers: the CAD schema is compiled to a **GBNF grammar
>   and enforced inside llama.cpp's sampler**, so every operation the model emits is
>   structurally valid by construction. [Set it up ↓](#local-models-no-api-key-no-cloud)
> - **Auto-repair harness** — failed generations, failed builds and defective geometry are
>   diagnosed and retried automatically for a configurable number of rounds (default 3, was a
>   single attempt), with a loop guard that stops if the model repeats a plan that already
>   failed. Python mode and the engineering timeline are now covered too.
> - **Errors the model can actually act on** — a build failure names the operation that failed
>   and what had already been built, instead of a bare kernel message.
> - **Deterministic fixes, no round-trip** — an oversized fillet/chamfer shrinks until the
>   geometry kernel accepts it and out-of-range edge indices are dropped, in milliseconds,
>   before any request is spent.
>
> **New in 2.4.1 — a lathe, and a hardening pass:**
> - **`revolve`** — spin a closed `[r, z]` profile around the Z axis. The IR's biggest
>   expressiveness gap: flanges, shafts, pulleys, bottles and vases stop needing a stack of
>   boolean primitives to approximate.
> - **Reasoning-model support across all three cloud providers** — thinking output shares the
>   token budget with the reply, so Claude 5 and GPT-5 requests now get a budget floor and a
>   longer timeout instead of arriving truncated or empty. GPT-5/o-series also take
>   `max_completion_tokens` and reject `temperature`; the adapter honours that.
> - **The Python-mode safety net is no longer bypassed on auto-run** — the deny-list is
>   skipped only when *you* press Build on code you can see.
> - **The local model now unloads with FreeCAD** (and on demand, from Settings), instead of
>   leaving a multi-gigabyte server resident after the app closes.
>
> **New in 2.5 — catching the mistakes that never looked like mistakes:**
> - **The whole program is inspected, not just the final object.** A plan that builds a
>   part and leaves a stray solid beside it used to finish "clean", because only the last
>   operation's result was ever measured. Every end product is now measured, and stray
>   geometry in a single-part build is reported as the defect it is.
> - **The values FreeCAD silently rewrites are now rejected.** Measured against FreeCAD 1.1:
>   a cylinder `angle` of 0, −30 or 400 all build a *full* 360° cylinder; a 400° `revolve`
>   builds 40°; a cone radius of −5 builds 0; a repeated profile point quietly drops a
>   corner (a four-point square became a triangle). None of these are errors to the geometry
>   kernel — they just build the wrong part. Each is now caught before the build, with a
>   message saying what FreeCAD would have done instead.
> - **A fillet too big for its edges no longer ships as a finished part.** It returned a
>   *non-null* self-intersecting solid — −21025 mm³ from a 1000 mm³ box — which passed the
>   old null-only check. Build results are now checked for validity and positive volume,
>   per operation, so the error names the step that caused it.
> - **Repair rounds see the measurements.** Instead of "the result has zero volume", the
>   model gets the volume and bounding box of every object, so it can see that the tool it
>   cut with sits 200 mm from the part it was cutting.
> - **Self-review** — when the geometry is sound but the largest dimension you asked for
>   appears nowhere in the built part, the model is shown your request alongside the
>   measurements and may correct it or sign it off. A build that matches your request costs
>   no extra request.
>
> **New in 2.6 — every model, not a shortlist someone hard-coded:**
> - **Model lists come from the provider, live.** Every list in this addon used to be a
>   Python literal, so a model released on Tuesday was invisible until someone edited the
>   source. **Browse…** next to any Model box fetches the provider's current catalogue and
>   opens a searchable table with context length, price per million tokens, and whether the
>   model can actually be asked for JSON. The box stays editable, so an id too new even for
>   the catalogue still works.
> - **OpenRouter** — one key for several hundred models across every major lab and the whole
>   open-weight ecosystem. Its catalogue is public, so you can browse the full list *before*
>   pasting a key. Free models are flagged and filterable.
> - **Ollama / LM Studio** — point at a server you are already running and use whatever you
>   have pulled there. No key, no cloud, and GPT4FreeCAD does not manage the weights.
>   **Detect** finds it on the usual ports (Ollama 11434, LM Studio 1234, Jan 1337).
> - **"Find on this PC…"** — lists the GGUF models already downloaded by LM Studio, GPT4All
>   or llama.cpp instead of making you remember where that app put them. Multimodal
>   projectors, embedding models and the tail shards of split models are filtered out, so
>   the list is only things that can actually hold a conversation.
> - **Structured mode filters for it.** The picker defaults to models that support a JSON
>   response, because a model that cannot be asked for one fails every single generation.
>
> **New in 2.7 — schema enforcement beyond the local model, and fewer pointless failures:**
> - **Renaming a feature no longer fails.** Engineering mode appends, so asking to change
>   something that exists made the model reuse its name — "a hole bored in the centre",
>   then "bore the hole through", and the step died on *object name 'hole' is already
>   used*. The append-only protocol creates that collision, so the addon now fixes it:
>   `hole` becomes `hole_2`, with a note, and no request is spent on it.
> - **A hole that misses the material is caught.** Asking only "did it remove anything"
>   let a 6 mm hole take 0.28 mm³ out of 6283 and call it a success. Holes are now
>   measured against the volume the drill actually sweeps.
> - **OpenRouter enforces the schema**, not just "please reply in JSON" — 263 of its 340
>   models accept a strict JSON schema, and the rest downgrade automatically after one
>   request. The same guarantee local models get from grammar enforcement.
> - **xAI (Grok)** joins as a provider, with its own key and live model list.
>
> **New in 2.8 — it's there when FreeCAD opens, and it shows its working:**
> - **No workbench hunting.** FreeCAD never initialises a workbench until you pick it from
>   the selector, so the panel was invisible until you went looking. It now opens on
>   startup and a **GPT4FreeCAD toolbar button sits in every workbench** — both switchable
>   off, along with an option to start FreeCAD in the GPT4FreeCAD workbench.
> - **A Thinking tab** — the model's reasoning for the last reply and what it cost
>   (input, output, thinking, cached tokens). Claude and Gemini return a readable summary;
>   local models and OpenAI-compatible servers return whatever they emit. Those traces
>   were already being paid for and thrown away.
> - **Inline `<think>` blocks no longer break the parse.** Left in the reply they defeated
>   the straight JSON read, so every local model that thinks out loud was paying for a
>   fallback path it didn't need.
> - **A Prompt tab** — the system prompt itself, editable, showing exactly what gets sent
>   for the current mode. Save your own or reset to the built-in; stored per mode.
>   Engineering steps always keep the program-so-far appended, whatever you write.

---

## What's new in 2.0

| | v1 (2023) | v2 (this) |
|---|---|---|
| Providers | OpenAI only | **Gemini, OpenAI, Claude, OpenRouter, or either kind of local model** (pluggable) |
| Model choice | hard-coded `gpt-4` | per-provider, editable dropdowns |
| Output | raw `exec()` of model text | **validated structured IR** → parametric objects, *or* opt-in Python mode |
| Keys | plaintext `~/api_key.txt` | FreeCAD preferences, per provider, password-masked |
| UI | modal dialog | **workbench + dockable panel** with settings |
| Reliability | hope | schema validation + **multi-round auto-repair harness** (+ grammar-enforced output on local models) |
| Dependencies | `requests` | **none** (stdlib only) |
| Threading | blocks FreeCAD | background worker, UI stays responsive |

---

## How it works

```
Your description ──▶ LLM (Gemini/OpenAI/Claude/local) ──▶ JSON program
                                                        │
                                          schema.validate_program()  ◀── auto-repair on error
                                            structure · types · refs
                                            + geometry the kernel would
                                              silently accept and get wrong
                                                        │
                                          interpreter.build_program()  ◀── auto-repair on error
                                            every operation checked for a
                                            valid, positive-volume result
                                                        │
                                          inspect: measure every end product ◀── auto-repair
                                                        │                         on a defect
                                          review against the request ─────────▶ correct or sign off
                                                        │                        (only if a
                                          parametric FreeCAD feature tree         measurement
                                                                                  disagrees)
```

The model is constrained to a small, safe vocabulary of CAD operations (the *IR*). Example
program for *a 40×40×10 plate with a centred 6 mm bore, edges filleted 2 mm*:

```json
{
  "operations": [
    {"op": "box", "name": "plate", "length": 40, "width": 40, "height": 10},
    {"op": "cylinder", "name": "bore", "radius": 6, "height": 12, "placement": {"pos": [20, 20, -1]}},
    {"op": "cut", "name": "result", "base": "plate", "tool": "bore"},
    {"op": "fillet", "name": "finished", "target": "result", "radius": 2}
  ]
}
```

Because every object is a native FreeCAD parametric feature (`Part::Box`, `Part::Cut`,
`Part::Fillet`, …), you can edit the result by hand afterwards just like any FreeCAD model.

### Supported operations

| Op | Purpose |
|---|---|
| `box`, `cylinder`, `sphere`, `cone`, `torus` | primitives |
| `extrude` | extrude a closed 2D polygon profile along +Z |
| `revolve` | revolve a closed `[r, z]` profile around the Z axis (lathe: flanges, shafts, pulleys, vases) |
| `cut`, `fuse`, `common` | boolean difference / union / intersection |
| `fillet`, `chamfer` | round / bevel edges (all edges, or by index) |
| `linear_pattern`, `polar_pattern` | replicate a feature in a grid / around an axis |
| `mirror` | mirror across XY/XZ/YZ (optionally fused for symmetry) |
| `shell` | hollow a solid to a wall thickness |
| `hole` | drill a hole, with optional counterbore / countersink |
| `translate`, `rotate` | reposition an existing object |

Every primitive supports an optional `placement` (`pos` + `rotation`). Dimensions are in the
unit you pick in the panel (mm/cm/m/in) and the model converts as needed.

---

## Requirements

- FreeCAD **0.20+** (works on 0.20/0.21 with PySide2 and 1.0+ with PySide6)
- Either an API key for one cloud provider:
  - [Google AI Studio](https://aistudio.google.com/app/apikey) (Gemini)
  - [OpenAI](https://platform.openai.com/api-keys)
  - [Anthropic](https://console.anthropic.com/settings/keys)
- …**or no key at all**, using a local model — see below.

No `pip install` is required — GPT4FreeCAD uses only the Python standard library plus the
PySide that ships with FreeCAD.

## Local models (no API key, no cloud)

Pick the **Local (Machine Activation)** provider to run a model on your own machine. No key,
no account, works offline, and nothing you design leaves the computer.

**Setup is one step: choose a `.gguf` file.**

**⚙ Settings → Local (Machine Activation) → Model → Choose…**, pick any GGUF model, save.
That's it. On first use GPT4FreeCAD downloads a `llama.cpp` inference backend for your
machine (~20 MB, once, cached in `~/.machine/llama-cpp` and shared with the
[Machine Activation SDK](https://github.com/revhappy/MachineActivationSDK)), loads your
model, and stops it when FreeCAD closes. No terminal, no server to start, and **no
`pip install`** — it uses only the Python that ships inside FreeCAD.

Loading a multi-gigabyte model takes a moment the first time you press Generate; it runs on
the background worker, so FreeCAD stays responsive, and the model stays loaded afterwards.

*Already running your own server?* Tick **Advanced** and point the server URL at it — the
model field can stay empty. If that server is
[`machine serve`](https://github.com/revhappy/MachineActivationSDK), **Test connection**
additionally reports the *activation contract*: whether the model actually fits this machine,
which acceleration it got (CPU/GPU/NPU), and anything degraded. That is the difference
between "the AI is slow" and "you are running a 7B model on CPU".

**Optional: a hard guarantee on the output shape.** When the local server is
`machine serve`, the CAD program schema — one branch per operation, carrying that operation's
required fields and types — is compiled to a GBNF grammar and enforced inside llama.cpp's
sampler. A `box` without its dimensions, a bad enum, a malformed vector: the model is *unable*
to produce them. Verified on `gemma-4-E4B-it` (IQ4_NL, CPU): plate-with-a-hole and
filleted-cube both produced valid programs on the first attempt.

GPT4FreeCAD detects this automatically and **does not** send a schema to a plain
`llama-server`, because llama.cpp's own schema-to-grammar conversion of this schema is
pathologically slow — measured on build b10182, even a single-operation schema returned nothing
in 40 s and the full one did not finish in 400 s. Constraining there would make a working model
look broken. Instead the prompt asks for JSON, the extractor is tolerant of fences and prose,
and the auto-repair harness fixes what slips through — exactly how the cloud providers that
have no grammar support have always worked here. So local models work either way; with
`machine serve` you additionally get the guarantee.

Nothing to install. GPT4FreeCAD downloads, starts and stops the backend using only the
standard library inside FreeCAD's own Python. `pip install machine-activation` is optional and
buys one thing: the activation report on **Test connection**.

## Installation

### Option A — Git clone into the Mod folder

Clone into your FreeCAD `Mod` directory:

- **Windows:** `%APPDATA%\FreeCAD\Mod`
- **Linux:** `~/.local/share/FreeCAD/Mod` (or `~/.FreeCAD/Mod`)
- **macOS:** `~/Library/Application Support/FreeCAD/Mod`

```bash
git clone https://github.com/revhappy/GPT4FreeCAD
```

Restart FreeCAD. The panel opens on startup and a **GPT4FreeCAD** toolbar button sits
in every workbench, so there is nothing to select — the workbench is there too, in the
workbench dropdown, for its full menu.

### Option B — Addon Manager

Once published, install via **Tools → Addon Manager** and search for *GPT4FreeCAD*.

## Usage

1. The panel is already open — FreeCAD starts with it docked on the right. If you closed
   it, press the **GPT4FreeCAD** toolbar button (present in every workbench), select the
   **GPT4FreeCAD** workbench, or run the `GPTSTART.FCMacro` macro.
2. Click the **⚙ settings** button and paste an API key for your provider. Pick a model.
3. Choose **Provider**, **Model**, **Mode**, and **Units**. Optionally tick **3D-print mode**.
4. Type a description and press **Generate** — or pick a **Template** to start from a
   ready-made parametric part with no API call.
5. The generated plan appears in the editable preview. With *auto-build* on it builds
   immediately; otherwise review/edit it and press **Build**.
6. **Undo** reverts the last build in one step; **Clear** resets the conversation.
7. **Save** (next to the Template picker) stores the current plan or timeline as your own
   reusable template.

You can refine iteratively — after a result, just say *"make it 20 mm taller"* or
*"add a 5 mm fillet to the top edges"* and it keeps the context.

### Where it appears

A FreeCAD workbench is invisible until you pick it from the workbench selector, so
GPT4FreeCAD does not rely on one. Three settings under **⚙ settings → FreeCAD
integration** control how it shows up, and all take effect without a restart:

| Setting | Default | What it does |
| --- | --- | --- |
| Open the panel when FreeCAD starts | on | Docks the panel on the right as FreeCAD comes up, whatever workbench you are in. It stays put when you switch workbenches. |
| Show the toolbar in every workbench | on | A GPT4FreeCAD button and a settings button, re-applied on every workbench change. |
| Start FreeCAD in the GPT4FreeCAD workbench | off | Sets FreeCAD's startup workbench. Unchecking restores your previous one. |

The commands are registered with FreeCAD proper (`GPT4FreeCAD_ShowPanel`,
`GPT4FreeCAD_Settings`), so you can also bind keyboard shortcuts to them or drop them
onto your own toolbars under **Tools → Customize**.

### Seeing and steering the model

Two tabs sit next to **Activity** and **Plan**, for when you want to know what the
model is doing rather than just what it produced:

- **Thinking** — the model's reasoning for the last reply, plus the token counts it
  cost (input, output, thinking, cached). Anthropic and Gemini return a readable
  summary of the reasoning; local models and OpenAI-compatible servers return
  whatever they emit, including inline `<think>` blocks. OpenAI's Chat Completions
  endpoint bills reasoning tokens but does not return the text, so the tab reports
  the count and says so rather than showing an empty box.
- **Prompt** — the system prompt itself, editable. The box always shows exactly
  what will be sent for the current mode, so the instructions are never hidden even
  if you never touch them. **Save prompt** uses your text from then on; **Reset**
  restores the built-in one. Overrides are stored per mode, since a prompt written
  for the JSON schema is not one that produces Python. Engineering steps always get
  the program built so far appended, whatever your prompt says — that is state, not
  instruction, and a step without it would re-derive the part from nothing.

Everything the model produces is editable before it becomes geometry: the **Plan**
tab holds the generated JSON (or Python) in a text editor, and **Build** builds what
is in the box — not what the model originally said. Generation parameters
(temperature, max tokens, auto-repair rounds) are in **⚙ settings**; provider, model,
mode, units and thinking level are on the panel itself.

### The three modes

- **Structured (casual)** — the default. Describe a whole part; the model returns validated
  JSON; no arbitrary code runs. Reliable and undo-friendly.
- **Engineering (step-by-step)** — build a part as an editable **feature timeline**. Press
  **+ AI step** to generate the next feature from your description, or **+ Manual** to add one
  by hand with a precise parameter form. Select any step to edit its exact parameters, reorder
  or delete steps, and the whole tree **rebuilds deterministically**. The model is given extra
  engineering discipline (datums, manufacturable dimensions, one feature per step).
- **Python (advanced)** — the model writes a FreeCAD Python script. Most flexible for exotic
  geometry, but it executes generated code (guarded by a small denylist). Use it when you trust
  the output.

Use the compact **Fused / Separate** selector before generating or adding an AI step. **Fused**
remains the default. **Separate** keeps new manufactured components as independent solids and
organizes visible results under **GPT4FreeCAD Assembly** without boolean-unioning them. Cuts,
holes, fillets, and other features can still be applied within each component.

### 3D-print mode

A toggle that composes with Structured *and* Engineering modes. When on, it:

- adds **printability rules** to the prompt (flat base, manifold solid, minimum walls, ≤45°
  overhangs, oversized fit holes, …) and the **build volume** (default 254 mm = 10 in per axis,
  editable in Settings);
- after each build, checks the bounding box against the bed and — if it's too big — offers a
  one-click **Scale to fit**;
- enables **Export STL…** (with configurable mesh deviation) to write a print-ready mesh.

---

## Architecture

The core is deliberately **decoupled from FreeCAD** so it can be unit-tested with plain
CPython:

```
gpt4freecad/
├── config.py          preferences + API-key storage (FreeCAD ParamGet / JSON fallback)
├── engine.py          orchestration: prompt → LLM → validated program / step   (pure)
├── util.py            small shared helpers                               (pure)
├── llm/               provider abstraction                               (pure)
│   ├── base.py        Provider ABC, HTTP, JSON extraction, ModelInfo, registry,
│                       Reply (text + reasoning trace + token usage)
│   ├── gemini.py · openai.py · anthropic.py · openrouter.py · grok.py
│   ├── local.py       Machine Activation SDK: local GGUF models, no key
│   ├── localserver.py Ollama / LM Studio: attach to a server you already run
│   ├── discovery.py   find GGUF models already downloaded on this PC   (pure)
├── cad/
│   ├── schema.py      CAD operation IR: validation + JSON schema         (pure)
│   ├── prompts.py     system prompts + engineering/print addenda         (pure)
│   ├── templates.py   built-in + user template library (starter programs) (pure)
│   ├── interpreter.py IR → parametric FreeCAD objects (build + rebuild)   (FreeCAD)
│   ├── inspect.py     whole-program geometry review: facts, problems,
│                       measurements, dimension check                     (pure)
│   ├── export.py      STL/STEP export, bounding box, scale-to-fit (+ pure overage)
│   └── pyrun.py       advanced Python-mode runner                        (FreeCAD)
├── ui/                qt shim · dockable panel · settings · worker       (PySide)
│   ├── panel.py       header, mode stack, casual + 3D-print controls
│   ├── engineering.py step-timeline widget (the feature history)
│   ├── op_form.py     schema-driven parameter form (one editor per field)
│   ├── model_picker.py searchable model table (filter, price, JSON support)
│   └── startup.py     workbench-independent entry points: auto-open panel,
│                       always-on toolbar, startup workbench       (FreeCAD)
└── workbench.py       Gui.Workbench + commands                          (FreeCAD)
```

Adding a provider = subclass `llm.base.Provider` and `@register` it.
Adding a CAD operation = add an entry to `cad.schema.OPERATIONS` and a handler in
`cad.interpreter`. The prompt, JSON schema, **and the engineering parameter form** all derive
themselves from that one table — no extra UI code.

## Development & tests

The FreeCAD-free core is fully unit-tested (providers are network-mocked):

```bash
python tests/test_core.py                # schema, prompts, inspect, providers, config
python tests/test_interpreter_stubbed.py # interpreter logic, FreeCAD stubbed out
```

The panel's build → inspect → repair → review decisions are tested too, by binding the
real methods to a stand-in object. That needs the Qt binding FreeCAD ships, so it runs
under FreeCAD's own interpreter (it reports itself skipped anywhere else):

```bash
"/c/Program Files/FreeCAD 1.1/bin/freecadcmd.exe" tests/test_panel_flow.py
```

`freecadcmd` is also the way to check real geometry behaviour rather than reason about
it — the geometry kernel accepts several out-of-range values and quietly builds
something else, which is how the 2.5 findings turned up.

## Tips — from the original 2023 release

> Yes, the model can fail and present a blank screen or something random.  
> You can turn on Report View, I have it set up to show the python code along with any errors.
> In case of failure, merely prompt 'try again' or copy and paste the error into the prompt screen.
> These are early days, be patient :p

*Kept for posterity — the updated version now validates every plan before it runs, inspects
the built geometry, and sends errors back to the model for an automatic fix. (But "try
again" still works too.)*

## License

MIT — see [LICENSE](LICENSE.MD). Copyright (c) 2023–2026 Robb Sharma.

## Links

- [GitHub Repository](https://github.com/revhappy/GPT4FreeCAD)
- [Changelog](CHANGELOG.md)
