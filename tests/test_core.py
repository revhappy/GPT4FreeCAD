"""Standalone tests for the FreeCAD-free core (schema, providers, engine, config).

Run with either::

    python tests/test_core.py
    pytest tests/test_core.py
"""

import json
import os
import sys
import tempfile

# Make the package importable when run directly from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpt4freecad.cad import schema, prompts, templates
from gpt4freecad.cad import inspect as ginspect
from gpt4freecad.config import Config, _JsonBackend
from gpt4freecad import engine, harness, util
from gpt4freecad.llm import (
    all_providers, get_provider, extract_json, ChatRequest, LLMError,
)
from gpt4freecad.llm import openai as openai_mod
from gpt4freecad.llm import anthropic as anthropic_mod
from gpt4freecad.llm import gemini as gemini_mod
from gpt4freecad.llm import local as local_mod


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
    assert captured["payload"]["temperature"] == 0.1


def test_openai_reasoning_model_request():
    """gpt-5.x / o-series: max_completion_tokens, no temperature, budget floor."""
    captured = _patch(openai_mod, {"choices": [{"message": {"content": "ok"}}]})
    req = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="gpt-5.1", json_mode=True, max_tokens=4096, temperature=0.2,
    )
    out = get_provider("openai").chat(req, "sk-test")
    assert out == "ok"
    payload = captured["payload"]
    assert "max_tokens" not in payload            # rejected by reasoning models
    assert "temperature" not in payload           # likewise
    assert payload["max_completion_tokens"] >= 16384  # reasoning shares the budget
    # Current defaults lead with the reasoning family.
    assert get_provider("openai").default_model.startswith("gpt-5")


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


def test_anthropic_request():
    captured = _patch(anthropic_mod, {"content": [{"type": "text", "text": '{"a": 1}'}]})
    req = ChatRequest(
        messages=[{"role": "system", "content": "sys"},
                  {"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6", json_mode=True, max_tokens=200,
    )
    out = get_provider("anthropic").chat(req, "ant-key")
    assert out == '{"a": 1}'
    assert captured["headers"]["x-api-key"] == "ant-key"
    assert captured["headers"]["anthropic-version"]
    assert captured["payload"]["system"] == "sys"
    # No assistant prefill: current Claude models reject it with HTTP 400.
    assert captured["payload"]["messages"][-1] == {"role": "user", "content": "hi"}
    assert captured["payload"]["max_tokens"] == 200
    assert "temperature" in captured["payload"]  # still accepted on sonnet-4-6


def test_anthropic_claude5_request():
    captured = _patch(anthropic_mod, {"content": [{"type": "text", "text": "ok"}]})
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}],
                      model="claude-fable-5", max_tokens=100)
    out = get_provider("anthropic").chat(req, "ant-key")
    assert out == "ok"
    assert "temperature" not in captured["payload"]  # rejected on Claude 5
    assert captured["payload"]["fallbacks"] == "default"
    assert captured["headers"]["anthropic-beta"]
    # Thinking-by-default models spend thinking from max_tokens; a small cap
    # truncates the answer before any text, so the adapter applies a floor.
    assert captured["payload"]["max_tokens"] >= 16384


def test_anthropic_non_thinking_model_keeps_its_cap():
    captured = _patch(anthropic_mod, {"content": [{"type": "text", "text": "ok"}]})
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}],
                      model="claude-sonnet-4-6", max_tokens=100)
    get_provider("anthropic").chat(req, "ant-key")
    assert captured["payload"]["max_tokens"] == 100  # no floor needed


def test_anthropic_refusal():
    _patch(anthropic_mod, {"content": [], "stop_reason": "refusal"})
    req = ChatRequest(messages=[{"role": "user", "content": "x"}],
                      model="claude-fable-5")
    expect_error(lambda: get_provider("anthropic").chat(req, "k"), LLMError)


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
    try:
        engine.generate(p, "k", "fake-1", "x", mode="structured")
    except schema.SchemaError as exc:
        # The reply is attached so the panel's repair rounds can echo it back -
        # a reply that never validated is not in the conversation history.
        assert exc.raw_reply == bad
    else:
        raise AssertionError("expected SchemaError")


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


def _schema_branches():
    """{op name: its schema branch} from the generated program schema."""
    items = schema.json_schema()["properties"]["operations"]["items"]
    return {b["properties"]["op"]["const"]: b for b in items["anyOf"]}


def test_new_ops_in_reference_and_schema():
    ref = schema.operations_reference()
    branches = _schema_branches()
    for op in ("linear_pattern", "polar_pattern", "mirror", "shell", "hole",
               "revolve"):
        assert op in ref
        assert op in branches


def test_schema_revolve():
    # A pulley-ish closed [r, z] profile revolved fully, then cut like any solid.
    schema.validate_program({"operations": [
        {"op": "revolve", "name": "pulley",
         "profile": [[5, 0], [20, 0], [20, 4], [12, 6], [12, 14], [20, 16],
                     [20, 20], [5, 20]]},
        {"op": "cylinder", "name": "keyway", "radius": 2, "height": 25},
        {"op": "cut", "name": "finished", "base": "pulley", "tool": "keyway"},
    ]})
    # Partial revolve via 'angle'; zero/negative angles are rejected.
    schema.validate_program({"operations": [
        {"op": "revolve", "name": "half", "angle": 180,
         "profile": [[0, 0], [5, 0], [5, 5]]}]})
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "revolve", "name": "r", "angle": 0,
         "profile": [[0, 0], [5, 0], [5, 5]]}]}), schema.SchemaError)
    # A profile needs at least 3 [r, z] points, like extrude.
    expect_error(lambda: schema.validate_program({"operations": [
        {"op": "revolve", "name": "r", "profile": [[0, 0], [5, 0]]}]}),
        schema.SchemaError)


def test_json_schema_carries_each_ops_required_fields():
    """The schema must constrain more than 'op'.

    A grammar built from a schema that only pinned 'op' happily produced
    {"op": "box"} with no name or dimensions - accepted by the sampler, then
    rejected by validate_program. Every op's required fields belong in the
    schema, and it must stay derived from OPERATIONS.
    """
    branches = _schema_branches()
    assert set(branches) == set(schema.OPERATIONS)
    for op, spec in schema.OPERATIONS.items():
        required = branches[op]["required"]
        assert required[0] == "op"
        for field in spec["required"]:
            assert field in required, f"{op} must require {field}"
            assert field in branches[op]["properties"]
        for field in spec["optional"]:
            assert field in branches[op]["properties"], f"{op} should allow {field}"

    # Field types survive the translation, including enums and vectors.
    assert branches["box"]["properties"]["length"] == {"type": "number"}
    assert branches["mirror"]["properties"]["plane"]["enum"] == ["XY", "XZ", "YZ"]
    assert branches["linear_pattern"]["properties"]["count"] == {"type": "integer"}
    assert branches["box"]["properties"]["placement"]["type"] == "object"
    assert branches["translate"]["properties"]["vector"]["maxItems"] == 3


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
    sp = prompts.system_prompt("inch")
    assert "inch" in sp
    assert "box(" in sp
    assert prompts.repair_prompt("oops").startswith("The previous")
    assert "FreeCAD" in prompts.PYTHON_SYSTEM_PROMPT


def test_repair_prompt_echoes_the_failed_reply():
    """A reply that never validated is not in the history - echo it back."""
    p = prompts.repair_prompt("field 'radius' must be a number",
                              failed_reply='{"operations": [{"op": "sphere"}]}')
    assert '"sphere"' in p
    assert "radius" in p
    # Without a reply the prompt must not carry an empty echo section.
    assert "previous reply" not in prompts.repair_prompt("oops")
    # Huge replies are truncated, not sent whole.
    assert len(prompts.repair_prompt("e", failed_reply="x" * 100000)) < 10000


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
# local provider (Machine Activation SDK)
# --------------------------------------------------------------------------- #
def _patch_local(captured, response):
    """Replace local._post_json / _sdk_client so no server is needed."""
    def fake_post(url, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout
        return response

    originals = (local_mod._post_json, local_mod._sdk_client)
    local_mod._post_json = fake_post
    local_mod._sdk_client = lambda base_url, timeout: None  # force HTTP fallback
    return originals


def _restore_local(originals):
    local_mod._post_json, local_mod._sdk_client = originals


def test_local_provider_is_registered_and_keyless():
    provider = get_provider("machine")
    assert provider.id == "machine"
    assert provider.requires_key is False
    # Keyless providers must still be listed for the panel's provider combo.
    assert "machine" in {p.id for p in all_providers()}


def test_local_chat_via_http_fallback():
    captured = {}
    originals = _patch_local(
        captured, {"choices": [{"message": {"content": "ok"}}]})
    try:
        provider = get_provider("machine")
        provider.base_url = "http://127.0.0.1:9999"
        req = ChatRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="local-model", max_tokens=256, temperature=0.3,
        )
        out = provider.chat(req, "")  # no API key needed
        assert out == "ok"
        assert captured["url"] == "http://127.0.0.1:9999/v1/chat/completions"
        assert captured["payload"]["messages"][0]["content"] == "hi"
        assert captured["payload"]["max_tokens"] == 256
        assert "response_format" not in captured["payload"]  # json_mode off
        assert captured["timeout"] >= 600  # local decode is slow; no short timeout
    finally:
        _restore_local(originals)
        get_provider("machine").base_url = local_mod.DEFAULT_BASE_URL


def _json_mode_request():
    return ChatRequest(
        messages=[{"role": "user", "content": "a plate"}],
        model="local-model", json_mode=True, json_schema=schema.json_schema(),
    )


def test_local_json_mode_sends_a_compiled_grammar():
    """Every local server gets a ready grammar, not a schema to convert.

    The addon used to probe for `machine serve` and skip enforcement entirely on
    a bare llama-server. Compiling the grammar ourselves makes that distinction
    irrelevant: llama.cpp takes `grammar` and enforces it in the sampler
    everywhere, so a small model cannot emit a malformed program on any server.
    """
    captured = {}
    originals = _patch_local(
        captured, {"choices": [{"message": {"content": '{"operations": []}'}}]})
    try:
        get_provider("machine").chat(_json_mode_request(), "")
        grammar = captured["payload"]["grammar"]
        assert "response_format" not in captured["payload"]
        assert grammar.startswith("root ::= ")
        # The grammar must pin each op's own fields, not just the op name -
        # otherwise {"op": "box"} with no dimensions is grammatical.
        assert '"\\"box\\""' in grammar
        assert '"\\"length\\"" ws ":" ws number' in grammar
        assert '"\\"operations\\""' in grammar
    finally:
        _restore_local(originals)
        local_mod._GRAMMAR_CACHE.clear()


def test_local_falls_back_when_a_server_ignores_the_grammar():
    """An empty reply to a grammar must try the schema spellings, not give up."""
    calls = []

    def fake_post(url, payload, timeout):
        calls.append(payload)
        if "grammar" in payload:
            return {"choices": [{"message": {"content": ""}}]}  # ignored it
        return {"choices": [{"message": {"content": '{"operations": []}'}}]}

    originals = (local_mod._post_json, local_mod._sdk_client)
    local_mod._post_json = fake_post
    local_mod._sdk_client = lambda base_url, timeout: None
    try:
        out = get_provider("machine").chat(_json_mode_request(), "")
        assert out == '{"operations": []}'
        assert "grammar" in calls[0]                       # grammar first
        assert calls[1]["response_format"]["type"] == "json_schema"
    finally:
        local_mod._post_json, local_mod._sdk_client = originals
        local_mod._GRAMMAR_CACHE.clear()


def test_local_grammar_is_compiled_once_per_schema():
    """The schema is identical every request; compiling it each time is waste."""
    captured = {}
    originals = _patch_local(
        captured, {"choices": [{"message": {"content": "{}"}}]})
    try:
        local_mod._GRAMMAR_CACHE.clear()
        provider = get_provider("machine")
        provider.chat(_json_mode_request(), "")
        provider.chat(_json_mode_request(), "")
        assert len(local_mod._GRAMMAR_CACHE) == 1
    finally:
        _restore_local(originals)
        local_mod._GRAMMAR_CACHE.clear()


def test_local_gbnf_matches_the_sdk_emitter():
    """The vendored compiler must agree with the SDK's parity fixture.

    This file is a copy of the SDK's gbnf.py; the SDK pins that against the
    TypeScript emitter. If the copy drifts, grammars stop matching what the
    SDK (and `machine serve`) would build for the same schema.
    """
    from gpt4freecad.llm.gbnf import json_schema_to_gbnf

    cases = [
        ({"type": "string"}, "root ::= string"),
        ({"enum": ["a", "b"]}, r'"\"a\"" | "\"b\""'),
        ({"type": "array", "items": {"type": "integer"}, "minItems": 1},
         r'"[" ws integer (ws "," ws integer)* ws "]"'),
    ]
    for case_schema, expected in cases:
        assert expected in json_schema_to_gbnf(case_schema)
    # The real CAD schema compiles, and fast.
    import time
    started = time.monotonic()
    grammar = json_schema_to_gbnf(schema.json_schema())
    assert time.monotonic() - started < 1.0
    assert grammar.count("::=") > 20


def test_local_empty_reply_and_missing_server_are_clear():
    captured = {}
    originals = _patch_local(captured, {"choices": []})
    try:
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}], model="m")
        expect_error(lambda: get_provider("machine").chat(req, ""), LLMError)
    finally:
        _restore_local(originals)

    # A down server must say how to start one, not just surface a socket error.
    hint = local_mod._not_running_hint("http://127.0.0.1:8177", OSError("refused"))
    assert "machine serve" in hint and "127.0.0.1:8177" in hint


def test_local_engine_structured_passes_schema_to_provider():
    """Structured generation must hand the provider a schema to constrain on."""
    seen = {}

    class Recorder:
        label = "rec"

        def chat(self, request, api_key):
            seen["schema"] = request.json_schema
            seen["json_mode"] = request.json_mode
            return json.dumps(schema.example_program())

    result = engine.generate(
        Recorder(), "k", "m", "a plate", mode="structured")
    assert result.program is not None
    assert seen["json_mode"] is True
    assert seen["schema"]["required"] == ["operations"]


def test_activate_model_is_a_noop_without_a_model_path():
    """Attach-only mode: no model configured means nothing to start."""
    assert local_mod.activate_model("", "http://127.0.0.1:1") is None


def test_activate_model_reports_a_missing_file_clearly():
    """A typo'd path must name the file, not surface a server error."""
    original = local_mod._reachable
    local_mod._reachable = lambda base_url: False
    try:
        missing = os.path.join(tempfile.gettempdir(), "gpt4freecad-no-such-model.gguf")
        try:
            local_mod.activate_model(missing, "http://127.0.0.1:1")
        except LLMError as exc:
            # Either the SDK is absent (install hint) or the file check fires;
            # both must name the model rather than blaming the network.
            assert missing in str(exc)
        else:
            raise AssertionError("expected LLMError for a missing model file")
    finally:
        local_mod._reachable = original


def test_activate_model_skips_when_a_server_is_already_serving():
    """Reuse beats loading a second copy of a multi-gigabyte model."""
    original = local_mod._reachable
    local_mod._reachable = lambda base_url: True
    try:
        assert local_mod.activate_model("/some/model.gguf", "http://127.0.0.1:8177") is None
    finally:
        local_mod._reachable = original


def test_port_is_parsed_from_the_base_url():
    assert local_mod._port_of("http://127.0.0.1:9123") == 9123
    assert local_mod._port_of("http://127.0.0.1") == 8177  # default
    assert local_mod._port_of("not a url") == 8177         # never raises


def test_config_machine_model_path():
    path = os.path.join(tempfile.gettempdir(), "gpt4freecad-test-model.json")
    if os.path.exists(path):
        os.remove(path)
    cfg = Config(_JsonBackend(path))
    try:
        assert cfg.machine_model_path() == ""  # attach-only by default
        cfg.set_machine_model_path("  C:/models/foo.gguf  ")
        assert cfg.machine_model_path() == "C:/models/foo.gguf"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_config_machine_base_url():
    path = os.path.join(tempfile.gettempdir(), "gpt4freecad-test-machine.json")
    if os.path.exists(path):
        os.remove(path)
    cfg = Config(_JsonBackend(path))
    try:
        assert cfg.machine_base_url() == "http://127.0.0.1:8177"
        cfg.set_machine_base_url("http://localhost:9000 ")
        assert cfg.machine_base_url() == "http://localhost:9000"
        cfg.set_machine_base_url("")  # blank falls back to the default
        assert cfg.machine_base_url() == "http://127.0.0.1:8177"
    finally:
        if os.path.exists(path):
            os.remove(path)


# --------------------------------------------------------------------------- #
# harness (auto-repair bookkeeping)
# --------------------------------------------------------------------------- #
def test_harness_budget_and_reset():
    s = harness.RepairSession(2)
    assert s.can_retry() and s.attempts == 0
    s.start_attempt()
    assert s.can_retry() and s.round_label == "1/2"
    s.start_attempt()
    assert not s.can_retry()
    s.reset(3)
    assert s.attempts == 0 and s.budget == 3 and s.can_retry()
    s.reset(0)
    assert not s.can_retry()  # 0 disables auto-repair entirely


def test_only_model_mistakes_are_worth_repairing():
    """The repair budget must not be spent on a bad key or a dead network."""
    from gpt4freecad.llm.base import AuthError, RateLimitError

    transient = LLMError("Network error: connection refused")
    transient.transient = True

    assert harness.is_model_output_error(schema.SchemaError("bad profile"))
    assert harness.is_model_output_error(LLMError("Model did not return valid JSON"))
    assert not harness.is_model_output_error(AuthError("HTTP 401"))
    assert not harness.is_model_output_error(RateLimitError("HTTP 429"))
    assert not harness.is_model_output_error(transient)
    assert not harness.is_model_output_error(None)
    assert not harness.is_model_output_error(ValueError("unrelated"))


def test_harness_repeat_detection():
    s = harness.RepairSession()
    ops = [{"op": "box", "name": "b", "length": 1, "width": 1, "height": 1}]
    assert not s.seen_failure(ops)
    s.note_failure(ops)
    # Same content, different object / key order -> still a repeat.
    same = [dict(reversed(list(ops[0].items())))]
    assert s.seen_failure(same)
    assert not s.seen_failure([{"op": "sphere", "name": "s", "radius": 2}])
    # Code payloads fingerprint too (whitespace-insensitive at the ends).
    s.note_failure("doc.recompute()\n")
    assert s.seen_failure("  doc.recompute()")
    s.reset()
    assert not s.seen_failure(ops)


# --------------------------------------------------------------------------- #
# HTTP retry policy
# --------------------------------------------------------------------------- #
def _patch_post(flaky):
    """Swap base._post_once + time for the duration of one test."""
    import types
    from gpt4freecad.llm import base as base_mod
    sleeps = []
    originals = (base_mod._post_once, base_mod.time)
    base_mod._post_once = flaky
    base_mod.time = types.SimpleNamespace(sleep=sleeps.append)
    return base_mod, originals, sleeps


def test_http_retries_transient_then_succeeds():
    calls = {"n": 0}

    def flaky(url, body, headers, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            err = LLMError("HTTP 503: overloaded")
            err.transient = True
            raise err
        return {"ok": True}

    base_mod, originals, sleeps = _patch_post(flaky)
    try:
        out = base_mod.http_post_json("https://x", {})
        assert out == {"ok": True}
        assert calls["n"] == 3 and len(sleeps) == 2
    finally:
        base_mod._post_once, base_mod.time = originals


def test_http_gives_up_after_budget_and_skips_auth():
    def always_503(url, body, headers, timeout):
        err = LLMError("HTTP 503")
        err.transient = True
        raise err

    base_mod, originals, sleeps = _patch_post(always_503)
    try:
        expect_error(lambda: base_mod.http_post_json("https://x", {}), LLMError)
        assert len(sleeps) == 2  # retried, then gave up
    finally:
        base_mod._post_once, base_mod.time = originals

    calls = {"n": 0}

    def auth_fail(url, body, headers, timeout):
        calls["n"] += 1
        raise base_mod.AuthError("HTTP 401")

    base_mod, originals, sleeps = _patch_post(auth_fail)
    try:
        expect_error(lambda: base_mod.http_post_json("https://x", {}),
                     base_mod.AuthError)
        assert calls["n"] == 1 and sleeps == []  # never retried
    finally:
        base_mod._post_once, base_mod.time = originals


# --------------------------------------------------------------------------- #
# repair prompts + config budget
# --------------------------------------------------------------------------- #
def test_python_repair_prompt():
    p = prompts.python_repair_prompt("NameError: box\n  at line 3: box.foo()")
    assert "NameError" in p and "line 3" in p and "```python```" in p


def test_step_repair_prompt():
    failed = [{"op": "fillet", "name": "f", "target": "ghost", "radius": 2}]
    p = prompts.step_repair_prompt("round the top", failed, "references undefined 'ghost'")
    assert "round the top" in p
    assert '"ghost"' in p          # the failed ops are echoed back as JSON
    assert "references undefined" in p
    assert "APPEND" in p


def test_config_repair_rounds():
    path = os.path.join(tempfile.gettempdir(), "gpt4freecad-test-repair.json")
    if os.path.exists(path):
        os.remove(path)
    cfg = Config(_JsonBackend(path))
    try:
        assert cfg.repair_rounds() == 3  # default
        cfg.set_repair_rounds(5)
        assert cfg.repair_rounds() == 5
        cfg.set_repair_rounds(99)
        assert cfg.repair_rounds() == 10  # clamped
        cfg.set_repair_rounds(-4)
        assert cfg.repair_rounds() == 0
    finally:
        if os.path.exists(path):
            os.remove(path)


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
