"""Standalone tests for the FreeCAD-free core (schema, providers, engine, config).

Run with either::

    python tests/test_core.py
    pytest tests/test_core.py
"""

import os
import sys
import tempfile

# Make the package importable when run directly from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpt4freecad.cad import schema, prompts, templates
from gpt4freecad.cad import inspect as ginspect
from gpt4freecad.config import Config, _JsonBackend
from gpt4freecad import engine, util
from gpt4freecad.llm import (
    all_providers, get_provider, extract_json, ChatRequest, LLMError,
)
from gpt4freecad.llm import openai as openai_mod
from gpt4freecad.llm import anthropic as anthropic_mod
from gpt4freecad.llm import gemini as gemini_mod


def expect_error(fn, exc=Exception):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} but none was raised")


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_schema_valid_example():
    ops = schema.validate_program(schema.example_program())
    assert len(ops) == 4
    assert ops[0]["op"] == "box"


def test_schema_accepts_bare_list():
    ops = schema.validate_program([{"op": "sphere", "name": "s", "radius": 5}])
    assert ops[0]["name"] == "s"


def test_schema_unknown_op():
    expect_error(lambda: schema.validate_program(
        {"operations": [{"op": "banana", "name": "b"}]}), schema.SchemaError)


def test_schema_missing_field():
    expect_error(lambda: schema.validate_program(
        {"operations": [{"op": "box", "name": "b", "length": 1, "width": 1}]}),
        schema.SchemaError)


def test_schema_bad_type():
    expect_error(lambda: schema.validate_program(
        {"operations": [{"op": "box", "name": "b", "length": "x", "width": 1, "height": 1}]}),
        schema.SchemaError)


def test_schema_non_positive():
    expect_error(lambda: schema.validate_program(
        {"operations": [{"op": "box", "name": "b", "length": 0, "width": 1, "height": 1}]}),
        schema.SchemaError)


def test_schema_undefined_reference():
    expect_error(lambda: schema.validate_program(
        {"operations": [{"op": "cut", "name": "r", "base": "nope", "tool": "also_nope"}]}),
        schema.SchemaError)


def test_schema_duplicate_name():
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "box", "name": "b", "length": 1, "width": 1, "height": 1},
        {"op": "box", "name": "b", "length": 1, "width": 1, "height": 1},
    ]}), schema.SchemaError)


def test_schema_forward_reference_rejected():
    # cut references 'tool' before it is defined -> error
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "box", "name": "base", "length": 1, "width": 1, "height": 1},
        {"op": "cut", "name": "r", "base": "base", "tool": "tool"},
        {"op": "cylinder", "name": "tool", "radius": 1, "height": 1},
    ]}), schema.SchemaError)


def test_schema_placement_validation():
    ok = {"operations": [{"op": "box", "name": "b", "length": 1, "width": 1, "height": 1,
                          "placement": {"pos": [1, 2, 3],
                                        "rotation": {"axis": [0, 0, 1], "angle": 45}}}]}
    schema.validate_program(ok)
    bad = {"operations": [{"op": "box", "name": "b", "length": 1, "width": 1, "height": 1,
                           "placement": {"pos": [1, 2]}}]}
    expect_error(lambda: schema.validate_program(bad), schema.SchemaError)


def test_schema_profile_and_fuse():
    schema.validate_program({"operations": [
        {"op": "extrude", "name": "p", "height": 5,
         "profile": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        {"op": "cylinder", "name": "c", "radius": 2, "height": 5},
        {"op": "fuse", "name": "u", "parts": ["p", "c"]},
    ]})


def test_operations_reference_and_json_schema():
    ref = schema.operations_reference()
    for op in schema.OPERATIONS:
        assert op in ref
    js = schema.json_schema()
    assert "operations" in js["properties"]


# --------------------------------------------------------------------------- #
# JSON extraction + code extraction
# --------------------------------------------------------------------------- #
def test_extract_json_clean():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    txt = "Sure!\n```json\n{\"a\": 2}\n```\nDone"
    assert extract_json(txt) == {"a": 2}


def test_extract_json_embedded():
    txt = 'prefix {"a": {"b": 3}} suffix }'
    assert extract_json(txt) == {"a": {"b": 3}}


def test_extract_json_invalid():
    expect_error(lambda: extract_json("no json here"), LLMError)


def test_extract_code():
    assert util.extract_code("x\n```python\nprint(1)\n```\ny") == "print(1)"
    assert util.extract_code("print(2)") == "print(2)"


# --------------------------------------------------------------------------- #
# providers (network mocked)
# --------------------------------------------------------------------------- #
def test_registry():
    ids = {p.id for p in all_providers()}
    assert {"openai", "anthropic", "gemini"} <= ids
    assert get_provider("gemini").id == "gemini"
    expect_error(lambda: get_provider("nope"), LLMError)


def _patch(mod, response):
    captured = {}

    def fake(url, payload, headers=None, timeout=120):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers or {}
        return response

    mod.http_post_json = fake
    return captured


def test_openai_request():
    captured = _patch(openai_mod, {"choices": [{"message": {"content": "ok"}}]})
    req = ChatRequest(
        messages=[{"role": "system", "content": "sys"},
                  {"role": "user", "content": "hi"}],
        model="gpt-4o", json_mode=True, max_tokens=100, temperature=0.1,
    )
    out = get_provider("openai").chat(req, "sk-test")
    assert out == "ok"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "gpt-4o"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] == 100


def test_gemini_request():
    captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    req = ChatRequest(
        messages=[{"role": "system", "content": "sys"},
                  {"role": "user", "content": "hi"}],
        model="gemini-2.5-flash", json_mode=True,
    )
    out = get_provider("gemini").chat(req, "g-key")
    assert out == "{}"
    assert "gemini-2.5-flash:generateContent" in captured["url"]
    assert "key=g-key" in captured["url"]
    assert captured["payload"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    # user role mapped correctly
    assert captured["payload"]["contents"][0]["role"] == "user"


def test_gemini3_request_temperature_and_budget():
    # Gemini 3 models must NOT send temperature (default 1.0) and need a budget floor.
    captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    req = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gemini-3.5-flash", json_mode=True, temperature=0.2, max_tokens=4096,
    )
    out = get_provider("gemini").chat(req, "g-key")
    assert out == "{}"
    cfg = captured["payload"]["generationConfig"]
    assert "temperature" not in cfg                       # omitted for gemini-3
    assert cfg["maxOutputTokens"] >= 8192                 # budget floor applied
    assert cfg["responseMimeType"] == "application/json"


def test_gemini3_variants_detected():
    from gpt4freecad.llm.gemini import _is_gemini3
    assert _is_gemini3("gemini-3.5-flash")
    assert _is_gemini3("gemini-3-flash-preview")
    assert _is_gemini3("gemini-3.1-pro-preview")
    assert _is_gemini3("models/gemini-3.1-pro-preview")
    assert not _is_gemini3("gemini-2.5-flash")
    # only the Gemini 3 family remains (1.x / 2.0 / 2.5 removed)
    models = get_provider("gemini").default_models
    assert "gemini-1.5-flash" not in models
    assert "gemini-2.0-flash" not in models
    assert "gemini-2.5-flash" not in models
    assert "gemini-2.5-pro" not in models
    assert all(m.startswith("gemini-3") for m in models)
    assert get_provider("gemini").default_model == "gemini-3.5-flash"


def test_gemini3_thinking_levels():
    for level in ("minimal", "low", "medium", "high"):
        captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                          model="gemini-3.5-flash", thinking_level=level)
        get_provider("gemini").chat(req, "g-key")
        assert captured["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": level}


def test_gemini3_thinking_default_omitted():
    for level in (None, "default", "bogus"):
        captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                          model="gemini-3.5-flash", thinking_level=level)
        get_provider("gemini").chat(req, "g-key")
        assert "thinkingConfig" not in captured["payload"]["generationConfig"]


def test_gemini3_pro_minimal_bumped_to_low():
    captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                      model="gemini-3.1-pro-preview", thinking_level="minimal")
    get_provider("gemini").chat(req, "g-key")
    assert captured["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_gemini25_ignores_thinking_level():
    captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                      model="gemini-2.5-flash", thinking_level="high")
    get_provider("gemini").chat(req, "g-key")
    assert "thinkingConfig" not in captured["payload"]["generationConfig"]


def test_gemini25_still_sends_temperature():
    captured = _patch(gemini_mod, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}],
                      model="gemini-2.5-flash", temperature=0.2)
    get_provider("gemini").chat(req, "g-key")
    assert captured["payload"]["generationConfig"]["temperature"] == 0.2


def test_gemini_blocked():
    _patch(gemini_mod, {"promptFeedback": {"blockReason": "SAFETY"}})
    req = ChatRequest(messages=[{"role": "user", "content": "x"}], model="gemini-2.5-flash")
    expect_error(lambda: get_provider("gemini").chat(req, "g-key"), LLMError)


def test_anthropic_request_prefill():
    captured = _patch(anthropic_mod, {"content": [{"type": "text", "text": '"a": 1}'}]})
    req = ChatRequest(
        messages=[{"role": "system", "content": "sys"},
                  {"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6", json_mode=True, max_tokens=200,
    )
    out = get_provider("anthropic").chat(req, "ant-key")
    assert out == '{"a": 1}'  # prefill "{" re-prepended
    assert captured["headers"]["x-api-key"] == "ant-key"
    assert captured["headers"]["anthropic-version"]
    assert captured["payload"]["system"] == "sys"
    assert captured["payload"]["messages"][-1] == {"role": "assistant", "content": "{"}
    assert captured["payload"]["max_tokens"] == 200


# --------------------------------------------------------------------------- #
# engine (provider mocked)
# --------------------------------------------------------------------------- #
class _FakeProvider:
    id = "fake"
    default_models = ["fake-1"]

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def chat(self, request, api_key):
        self.calls.append(request)
        return self._replies.pop(0)


def test_engine_structured_ok():
    prog = '{"operations": [{"op": "box", "name": "b", "length": 1, "width": 1, "height": 1}]}'
    p = _FakeProvider([prog])
    res = engine.generate(p, "key", "fake-1", "a 1mm cube", mode="structured")
    assert res.mode == "structured"
    assert res.program[0]["op"] == "box"
    assert res.repaired is False
    # system prompt mentions units + ops
    assert "operations" in p.calls[0].messages[0]["content"]


def test_engine_structured_repair():
    bad = '{"operations": [{"op": "box", "name": "b"}]}'  # missing dims -> SchemaError
    good = '{"operations": [{"op": "sphere", "name": "s", "radius": 3}]}'
    p = _FakeProvider([bad, good])
    res = engine.generate(p, "key", "fake-1", "a ball", mode="structured")
    assert res.repaired is True
    assert res.program[0]["op"] == "sphere"
    assert len(p.calls) == 2  # original + repair


def test_engine_structured_repair_fails():
    bad = '{"operations": [{"op": "box", "name": "b"}]}'
    p = _FakeProvider([bad, bad])
    expect_error(lambda: engine.generate(p, "k", "fake-1", "x", mode="structured"),
                 schema.SchemaError)


def test_engine_python_mode():
    reply = "Here you go:\n```python\nbox = doc.addObject('Part::Box','B')\n```"
    p = _FakeProvider([reply])
    res = engine.generate(p, "key", "fake-1", "a box", mode="python")
    assert res.mode == "python"
    assert "addObject" in res.code
    assert p.calls[0].json_mode is False


def test_engine_forwards_thinking_level():
    prog = '{"operations": [{"op": "sphere", "name": "s", "radius": 3}]}'
    p = _FakeProvider([prog])
    engine.generate(p, "k", "fake-1", "ball", mode="structured", thinking_level="high")
    assert p.calls[0].thinking_level == "high"


def test_engine_engineering_and_print_prompt():
    prog = '{"operations": [{"op": "box", "name": "b", "length": 1, "width": 1, "height": 1}]}'
    p = _FakeProvider([prog])
    engine.generate(p, "k", "fake-1", "a part", mode="engineering",
                    print_profile={"bed": [254, 254, 254]})
    system = p.calls[0].messages[0]["content"]
    assert "ENGINEERING DISCIPLINE" in system
    assert "3D-PRINTING CONSTRAINTS" in system


def test_engine_separate_component_prompt():
    prog = '{"operations": [{"op": "box", "name": "a", "length": 1, "width": 1, "height": 1}]}'
    p = _FakeProvider([prog])
    engine.generate(p, "k", "fake-1", "two-piece housing", mode="engineering",
                    part_layout="separate", print_profile={"bed": [254, 254, 254]})
    system = p.calls[0].messages[0]["content"]
    assert "SEPARATE COMPONENT ASSEMBLY" in system
    assert "Keep components separate" in system
    assert "final result MUST be ONE" not in system


def test_generate_step_returns_new_ops_only():
    program = [{"op": "box", "name": "base", "length": 40, "width": 40, "height": 8}]
    reply = ('{"operations": [{"op": "cylinder", "name": "boss", "radius": 5, '
             '"height": 10, "placement": {"pos": [20, 20, 8]}}]}')
    p = _FakeProvider([reply])
    res = engine.generate_step(p, "k", "fake-1", program, "add a centred boss")
    assert res.mode == "engineering"
    assert len(res.program) == 1 and res.program[0]["name"] == "boss"
    assert "STEP MODE" in p.calls[0].messages[0]["content"]
    assert "base" in p.calls[0].messages[0]["content"]  # existing names listed


def test_generate_step_separate_layout_prompt():
    program = [{"op": "box", "name": "base", "length": 40, "width": 40, "height": 8}]
    reply = ('{"operations": [{"op": "box", "name": "cover", "length": 40, '
             '"width": 40, "height": 2, "placement": {"pos": [0, 0, 10]}}]}')
    p = _FakeProvider([reply])
    engine.generate_step(p, "k", "fake-1", program, "add a removable cover",
                         part_layout="separate")
    assert "SEPARATE COMPONENT ASSEMBLY" in p.calls[0].messages[0]["content"]


def test_generate_step_repair_on_bad_ref():
    program = [{"op": "box", "name": "base", "length": 1, "width": 1, "height": 1}]
    bad = '{"operations": [{"op": "cut", "name": "r", "base": "base", "tool": "ghost"}]}'
    good = '{"operations": [{"op": "fillet", "name": "f", "target": "base", "radius": 1}]}'
    p = _FakeProvider([bad, good])
    res = engine.generate_step(p, "k", "fake-1", program, "round it")
    assert res.repaired is True
    assert res.program[0]["op"] == "fillet"
    assert len(p.calls) == 2


def test_generate_step_first_from_empty():
    reply = '{"operations": [{"op": "box", "name": "base", "length": 30, "width": 30, "height": 5}]}'
    p = _FakeProvider([reply])
    res = engine.generate_step(p, "k", "fake-1", [], "a base plate")
    assert res.program[0]["name"] == "base"
    assert "first step" in p.calls[0].messages[0]["content"]


# --------------------------------------------------------------------------- #
# export helpers + new schema ops + prompts
# --------------------------------------------------------------------------- #
def test_export_overage_and_factor():
    from gpt4freecad.cad import export
    assert export.overage([280, 120, 60], [254, 254, 254]) == [26.0, 0.0, 0.0]
    assert export.fits([100, 100, 100], [254, 254, 254]) is True
    assert export.fits([300, 10, 10], [254, 254, 254]) is False
    assert abs(export.fit_factor([508, 100, 100], [254, 254, 254]) - 0.5) < 1e-9
    assert export.fit_factor([10, 10, 10], [254, 254, 254]) >= 1.0


def test_schema_new_ops_valid():
    schema.validate_program({"operations": [
        {"op": "box", "name": "b", "length": 10, "width": 10, "height": 5},
        {"op": "linear_pattern", "name": "row", "source": "b",
         "direction": [1, 0, 0], "count": 3, "spacing": 20},
        {"op": "polar_pattern", "name": "ring", "source": "b", "count": 6, "angle": 360},
        {"op": "mirror", "name": "mir", "source": "b", "plane": "xz", "combine": True},
        {"op": "shell", "name": "sh", "source": "b", "thickness": 2, "open_faces": [1]},
        {"op": "hole", "name": "drilled", "target": "b", "diameter": 4, "depth": 5,
         "position": [5, 5, 5], "through": False, "cbore_diameter": 8, "cbore_depth": 2},
    ]})


def test_schema_enum_int_bool():
    # bad enum
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "box", "name": "b", "length": 1, "width": 1, "height": 1},
        {"op": "mirror", "name": "m", "source": "b", "plane": "DIAGONAL"}]}),
        schema.SchemaError)
    # non-integer count
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "box", "name": "b", "length": 1, "width": 1, "height": 1},
        {"op": "linear_pattern", "name": "p", "source": "b",
         "direction": [1, 0, 0], "count": 3.5, "spacing": 10}]}),
        schema.SchemaError)
    # count must be > 0
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "box", "name": "b", "length": 1, "width": 1, "height": 1},
        {"op": "polar_pattern", "name": "p", "source": "b", "count": 0}]}),
        schema.SchemaError)
    # bool field given a string
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "box", "name": "b", "length": 1, "width": 1, "height": 1},
        {"op": "mirror", "name": "m", "source": "b", "plane": "XY", "combine": "yes"}]}),
        schema.SchemaError)


def test_validate_op_against_defined_names():
    schema.validate_op({"op": "cut", "name": "r", "base": "a", "tool": "b"}, ["a", "b"])
    expect_error(
        lambda: schema.validate_op({"op": "cut", "name": "r", "base": "a", "tool": "x"}, ["a"]),
        schema.SchemaError)


def test_new_ops_in_reference_and_schema():
    ref = schema.operations_reference()
    for op in ("linear_pattern", "polar_pattern", "mirror", "shell", "hole"):
        assert op in ref
        assert op in schema.json_schema()["properties"]["operations"]["items"]["properties"]["op"]["enum"]


def test_prompt_addenda_and_units():
    from gpt4freecad.cad import prompts
    sp = prompts.system_prompt("mm", engineering=True, print_profile={"bed": [254, 254, 254]})
    assert "ENGINEERING DISCIPLINE" in sp and "3D-PRINTING CONSTRAINTS" in sp and "254" in sp
    # bed converted to working units
    spi = prompts.system_prompt("in", print_profile={"bed": [254, 254, 254]})
    assert "10.0" in spi
    # casual prompt has no addenda
    casual = prompts.system_prompt("mm")
    assert "ENGINEERING DISCIPLINE" not in casual and "3D-PRINTING" not in casual
    # step prompt lists existing names
    step = prompts.step_system_prompt("mm", [{"op": "box", "name": "base",
                                              "length": 1, "width": 1, "height": 1}])
    assert "STEP MODE" in step and "base" in step
    separate = prompts.system_prompt(
        "mm", engineering=True, part_layout="separate",
        print_profile={"bed": [254, 254, 254]})
    assert "SEPARATE COMPONENT ASSEMBLY" in separate
    assert "independently watertight" in separate


# --------------------------------------------------------------------------- #
# inspection (post-build geometry checks)
# --------------------------------------------------------------------------- #
def _good_facts(**over):
    facts = {"name": "part", "null": False, "valid": True, "solids": 1,
             "closed": True, "volume": 1000.0, "bbox": [10.0, 10.0, 10.0]}
    facts.update(over)
    return facts


def test_inspect_clean_facts():
    assert ginspect.problems(_good_facts()) == []
    s = ginspect.summary(_good_facts())
    assert "part" in s and "1000.0" in s and "10.0" in s


def test_inspect_null_shape():
    probs = ginspect.problems({"name": "r", "null": True})
    assert probs and "no geometry" in probs[0]
    assert "no geometry" in ginspect.summary({"name": "r", "null": True})


def test_inspect_zero_volume():
    probs = ginspect.problems(_good_facts(volume=0.0))
    assert any("volume" in p for p in probs)


def test_inspect_invalid_and_open():
    assert any("validity" in p for p in ginspect.problems(_good_facts(valid=False)))
    assert any("watertight" in p for p in ginspect.problems(_good_facts(closed=False)))


def test_inspect_no_solids():
    probs = ginspect.problems(_good_facts(solids=0, volume=0.0))
    assert any("no solid" in p for p in probs)


def test_inspect_expect_single():
    # several solids are fine for separate layouts...
    assert ginspect.problems(_good_facts(solids=3)) == []
    # ...but flagged when one fused part was expected
    probs = ginspect.problems(_good_facts(solids=3), expect_single=True)
    assert any("disconnected" in p for p in probs)


def test_inspect_object_duck_typed():
    class Shell:
        def isClosed(self):
            return True

    class Solid:
        Shells = [Shell()]

    class BB:
        XLength, YLength, ZLength = 10.0, 20.0, 5.0

    class Shape:
        Volume = 999.0
        BoundBox = BB()
        Solids = [Solid()]

        def isNull(self):
            return False

        def isValid(self):
            return True

    class Obj:
        Label = "thing"
        Name = "thing"

    Obj.Shape = Shape()  # class bodies can't see enclosing-function names
    facts = ginspect.inspect_object(Obj())
    assert facts["valid"] and facts["solids"] == 1 and facts["closed"]
    assert facts["volume"] == 999.0 and facts["bbox"] == [10.0, 20.0, 5.0]
    assert ginspect.problems(facts) == []


def test_defaults_addendum_in_prompts():
    # engineering defaults appear in casual AND engineering structured prompts
    for sp in (prompts.system_prompt("mm"),
               prompts.system_prompt("mm", engineering=True)):
        assert "DEFAULT ENGINEERING VALUES" in sp
        assert "3.4" in sp and "4.5" in sp and "5.5" in sp  # M3/M4/M5 clearance
        assert "2.0-3.0 mm" in sp                            # wall thickness


def test_geometry_repair_prompt():
    msg = prompts.geometry_repair_prompt("result has zero volume")
    assert "zero volume" in msg
    assert "corrected" in msg and "operations" in msg


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_config_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        cfg = Config(backend=_JsonBackend(os.path.join(d, "c.json")))
        cfg.set_api_key("gemini", "  g123  ")
        assert cfg.api_key("gemini") == "g123"  # trimmed
        cfg.set_provider("anthropic")
        assert cfg.provider() == "anthropic"
        cfg.set_model("openai", "gpt-4o")
        assert cfg.model("openai", "default") == "gpt-4o"
        assert cfg.model("gemini", "fallback") == "fallback"
        cfg.set_temperature(0.7)
        assert abs(cfg.temperature() - 0.7) < 1e-9
        cfg.set_max_tokens(2048)
        assert cfg.max_tokens() == 2048
        cfg.set_auto_run(False)
        assert cfg.auto_run() is False
        assert cfg.thinking_level() == "low"   # default
        cfg.set_thinking_level("high")
        assert cfg.thinking_level() == "high"

        # 3D-print settings
        assert cfg.mode() == "structured"
        assert cfg.part_layout() == "fused"
        cfg.set_part_layout("separate")
        assert cfg.part_layout() == "separate"
        assert cfg.print_mode() is False
        assert cfg.print_profile() is None
        assert cfg.bed_x() == 254.0 and cfg.bed_y() == 254.0 and cfg.bed_z() == 254.0
        assert abs(cfg.stl_deflection() - 0.1) < 1e-9
        cfg.set_print_mode(True)
        cfg.set_bed(200, 200, 180)
        cfg.set_stl_deflection(0.05)
        assert cfg.print_profile() == {"bed": [200.0, 200.0, 180.0]}
        assert abs(cfg.stl_deflection() - 0.05) < 1e-9

        # persistence across instances
        cfg2 = Config(backend=_JsonBackend(os.path.join(d, "c.json")))
        assert cfg2.api_key("gemini") == "g123"


def test_prompts_build():
    sp = prompts.structured_system_prompt("inch")
    assert "inch" in sp
    assert "box(" in sp
    assert prompts.repair_prompt("oops").startswith("The previous")
    assert "FreeCAD" in prompts.PYTHON_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
def test_templates_builtins_are_valid_programs():
    ts = templates.builtin_templates()
    ids = [t["id"] for t in ts]
    assert len(set(ids)) == len(ids)
    for t in ts:
        assert t["name"] and t["description"]
        ops = schema.validate_program({"operations": t["operations"]})
        assert ops


def test_templates_cover_the_common_parts():
    ids = {t["id"] for t in templates.builtin_templates()}
    for expected in ("flange", "l-bracket", "enclosure", "gear-blank",
                     "mounting-plate", "spacer"):
        assert expected in ids, f"missing built-in template '{expected}'"


def test_template_program_is_a_deep_copy():
    template = templates.builtin_templates()[0]
    program = templates.template_program(template)
    original_name = template["operations"][0]["name"]
    program["operations"][0]["name"] = "mutated"
    assert templates.builtin_templates()[0]["operations"][0]["name"] == original_name


def test_user_template_save_and_load():
    with tempfile.TemporaryDirectory() as d:
        os.environ["GPT4FREECAD_TEMPLATES"] = d
        try:
            ops = [{"op": "box", "name": "b", "length": 1, "width": 2, "height": 3}]
            path = templates.save_user_template("My Bracket!", ops, "test part")
            assert os.path.basename(path) == "my-bracket.json"
            assert os.path.exists(path)

            loaded, problems = templates.user_templates()
            assert problems == []
            assert len(loaded) == 1
            assert loaded[0]["name"] == "My Bracket!"
            assert loaded[0]["description"] == "test part"
            assert loaded[0]["operations"] == ops
            assert loaded[0]["user"] is True

            # saving under the same name replaces the file, not duplicates it
            templates.save_user_template("My Bracket!", ops)
            loaded, _ = templates.user_templates()
            assert len(loaded) == 1
        finally:
            del os.environ["GPT4FREECAD_TEMPLATES"]


def test_user_template_rejects_invalid_input():
    with tempfile.TemporaryDirectory() as d:
        os.environ["GPT4FREECAD_TEMPLATES"] = d
        try:
            expect_error(lambda: templates.save_user_template(
                "bad", [{"op": "banana", "name": "b"}]), schema.SchemaError)
            expect_error(lambda: templates.save_user_template("  ", []), ValueError)

            # corrupt files are reported as problems, not fatal
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            loaded, problems = templates.user_templates()
            assert loaded == []
            assert len(problems) == 1 and "broken.json" in problems[0]
        finally:
            del os.environ["GPT4FREECAD_TEMPLATES"]


def test_user_templates_missing_dir_is_empty():
    os.environ["GPT4FREECAD_TEMPLATES"] = os.path.join(
        tempfile.gettempdir(), "gpt4freecad-no-such-dir")
    try:
        loaded, problems = templates.user_templates()
        assert loaded == [] and problems == []
    finally:
        del os.environ["GPT4FREECAD_TEMPLATES"]


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
