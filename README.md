# GPT4FreeCAD

Generate **parametric** FreeCAD geometry from plain English — powered by your choice of
**Google Gemini, OpenAI, Anthropic Claude, or a local model on your own machine**.

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

---

## What's new in 2.0

| | v1 (2023) | v2 (this) |
|---|---|---|
| Providers | OpenAI only | **Gemini, OpenAI, Claude, or a local model** (pluggable) |
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
Your description ──▶ LLM (Gemini/OpenAI/Claude) ──▶ JSON program
                                                        │
                                          schema.validate_program()  ◀── auto-repair on error
                                                        │
                                          interpreter.build_program()
                                                        │
                                          parametric FreeCAD feature tree
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

It talks to a [Machine Activation SDK](https://github.com/revhappy/MachineActivationSDK)
server — `llama.cpp` under the hood, so any GGUF model works:

```bash
npm i machineai-activation          # provides the `machine` CLI
machine serve ./models/your-model.gguf
```

Then in FreeCAD: **⚙ Settings → Local (Machine Activation)**, confirm the server URL
(default `http://127.0.0.1:8177`), and press **Test connection**. Rather than a generic
"OK" it reports the *activation contract* — whether the model actually fits this machine,
which acceleration it got (CPU/GPU/NPU), and anything degraded. That is the difference
between "the AI is slow" and "you are running a 7B model on CPU".

**Structured mode gets a hard guarantee locally.** The CAD program schema — one branch per
operation, carrying that operation's required fields and types — is compiled to a GBNF grammar
and enforced inside llama.cpp's sampler. A `box` without its dimensions, a bad enum, a
malformed vector: the model is *unable* to produce them. Asking a 4B model nicely for JSON
fails constantly; this cannot.

The grammar guarantees each operation's shape. Cross-operation rules — references resolving to
objects defined earlier, unique names, positive dimensions — stay with the validator, backed by
the auto-repair harness above. Verified on `gemma-4-E4B-it` (IQ4_NL, CPU): plate-with-a-hole
and filleted-cube both produced valid programs on the first attempt, no repair round needed.

Optionally `pip install machine-activation` (dependency-free, stdlib only) for the full
activation report. Without it GPT4FreeCAD speaks the same HTTP directly, so the provider
still works in FreeCAD's bundled Python.

## Installation

### Option A — Git clone into the Mod folder

Clone into your FreeCAD `Mod` directory:

- **Windows:** `%APPDATA%\FreeCAD\Mod`
- **Linux:** `~/.local/share/FreeCAD/Mod` (or `~/.FreeCAD/Mod`)
- **macOS:** `~/Library/Application Support/FreeCAD/Mod`

```bash
git clone https://github.com/revhappy/GPT4FreeCAD
```

Restart FreeCAD. **GPT4FreeCAD** will appear in the workbench dropdown.

### Option B — Addon Manager

Once published, install via **Tools → Addon Manager** and search for *GPT4FreeCAD*.

## Usage

1. Select the **GPT4FreeCAD** workbench (or run the `GPTSTART.FCMacro` macro).
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
│   ├── base.py        Provider ABC, HTTP, JSON extraction, registry
│   ├── gemini.py  ·  openai.py  ·  anthropic.py
│   ├── local.py       Machine Activation SDK: local GGUF models, no key
├── cad/
│   ├── schema.py      CAD operation IR: validation + JSON schema         (pure)
│   ├── prompts.py     system prompts + engineering/print addenda         (pure)
│   ├── templates.py   built-in + user template library (starter programs) (pure)
│   ├── interpreter.py IR → parametric FreeCAD objects (build + rebuild)   (FreeCAD)
│   ├── inspect.py     post-build geometry checks (facts + problems)      (pure)
│   ├── export.py      STL/STEP export, bounding box, scale-to-fit (+ pure overage)
│   └── pyrun.py       advanced Python-mode runner                        (FreeCAD)
├── ui/                qt shim · dockable panel · settings · worker       (PySide)
│   ├── panel.py       header, mode stack, casual + 3D-print controls
│   ├── engineering.py step-timeline widget (the feature history)
│   └── op_form.py     schema-driven parameter form (one editor per field)
└── workbench.py       Gui.Workbench + commands                          (FreeCAD)
```

Adding a provider = subclass `llm.base.Provider` and `@register` it.
Adding a CAD operation = add an entry to `cad.schema.OPERATIONS` and a handler in
`cad.interpreter`. The prompt, JSON schema, **and the engineering parameter form** all derive
themselves from that one table — no extra UI code.

## Development & tests

The FreeCAD-free core is fully unit-tested (providers are network-mocked):

```bash
python tests/test_core.py     # or: pytest tests/test_core.py
```

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
