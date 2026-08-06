"""Orchestration: prompt -> LLM -> validated program / code.

Pure (no FreeCAD): this is the part that runs on a background thread. The
returned :class:`GenerationResult` is then handed back to the main thread, which
calls the FreeCAD interpreter to actually build geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import util
from .cad import prompts, schema
from .llm import ChatRequest, Provider, extract_json, reasoning_of, usage_of
from .llm.base import LLMError


@dataclass
class GenerationResult:
    mode: str
    raw: str
    program: Optional[List[Dict[str, Any]]] = None  # structured mode
    code: Optional[str] = None                       # python mode
    repaired: bool = False
    messages: List[Dict[str, str]] = field(default_factory=list)
    # Deterministic corrections applied to the model's reply before validating,
    # surfaced in the activity log so a silent fix is never actually silent.
    notes: List[str] = field(default_factory=list)
    # What the model thought on the way to this answer, and what it cost. Both
    # are reported by the provider when it can; empty when it cannot.
    reasoning: str = ""
    usage: Dict[str, int] = field(default_factory=dict)


def _meta(reply) -> Dict[str, Any]:
    """The reasoning/usage a provider attached to a reply, as result fields."""
    return {"reasoning": reasoning_of(reply), "usage": usage_of(reply)}


def generate(
    provider: Provider,
    api_key: str,
    model: str,
    description: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    mode: str = "structured",
    units: str = "mm",
    temperature: float = 0.2,
    max_tokens: int = 4096,
    thinking_level: Optional[str] = None,
    print_profile=None,
    part_layout: str = "fused",
    system_prompt: Optional[str] = None,
) -> GenerationResult:
    """Run one generation. Raises LLMError / SchemaError on unrecoverable failure.

    ``system_prompt`` replaces the generated instructions verbatim when set -
    the escape hatch for a user who wants to steer the model directly. Everything
    downstream still validates, so a bad prompt fails loudly rather than quietly
    building the wrong thing.
    """
    history = list(history or [])

    if mode == "python":
        return _generate_python(
            provider, api_key, model, description, history,
            temperature, max_tokens, thinking_level, system_prompt,
        )
    return _generate_structured(
        provider, api_key, model, description, history,
        units, temperature, max_tokens, thinking_level,
        engineering=(mode == "engineering"), print_profile=print_profile,
        part_layout=part_layout, system_prompt=system_prompt,
    )


def _generate_python(provider, api_key, model, description, history,
                     temperature, max_tokens, thinking_level,
                     system_prompt=None):
    system = system_prompt or prompts.PYTHON_SYSTEM_PROMPT
    messages = [{"role": "system", "content": system}]
    messages += history
    messages.append({"role": "user", "content": description})

    raw = provider.chat(
        ChatRequest(messages=messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, json_mode=False, thinking_level=thinking_level),
        api_key,
    )
    code = util.extract_code(raw)
    if not code:
        raise LLMError("Model reply contained no Python code.")
    return GenerationResult(mode="python", raw=raw, code=code, messages=messages,
                            **_meta(raw))


def _generate_structured(
    provider, api_key, model, description, history,
    units, temperature, max_tokens, thinking_level,
    engineering=False, print_profile=None, part_layout="fused",
    system_prompt=None,
):
    system = system_prompt or prompts.system_prompt(
        units, engineering=engineering, print_profile=print_profile,
        part_layout=part_layout)
    messages = [{"role": "system", "content": system}]
    messages += history
    messages.append({"role": "user", "content": description})

    raw = provider.chat(
        ChatRequest(messages=messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                    json_schema=schema.json_schema(),
                    json_schema_strict=schema.json_schema(strict=True)),
        api_key,
    )

    try:
        program, notes = _validated_program(raw)
        return GenerationResult(mode="structured", raw=raw, program=program,
                                messages=messages, notes=notes, **_meta(raw))
    except (LLMError, schema.SchemaError) as first_error:
        # One automatic repair attempt: show the model its own output + the error.
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": prompts.repair_prompt(str(first_error))},
        ]
        raw2 = provider.chat(
            ChatRequest(messages=repair_messages, model=model, temperature=temperature,
                        max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                        json_schema=schema.json_schema(),
                        json_schema_strict=schema.json_schema(strict=True)),
            api_key,
        )
        try:
            program, notes = _validated_program(raw2)
        except (LLMError, schema.SchemaError) as second_error:
            # The panel's repair rounds need the reply itself - without it the
            # model is asked to fix a program it cannot see.
            second_error.raw_reply = raw2
            raise
        return GenerationResult(
            mode="structured", raw=raw2, program=program, repaired=True,
            messages=messages, notes=notes, **_meta(raw2)
        )


def _validated_program(raw: str):
    """Parse, deterministically correct, then validate. Returns ``(ops, notes)``.

    Reusing an object name is the one mistake worth fixing here rather than
    sending back: the correction never needs judgement, and a repair round for
    it costs a request to arrive at the same answer.
    """
    data = extract_json(raw)
    operations = data.get("operations") if isinstance(data, dict) else data
    if not isinstance(operations, list):
        return schema.validate_program(data), []
    operations, notes = schema.dedupe_names(operations)
    return schema.validate_program({"operations": operations}), notes


def generate_step(
    provider: Provider,
    api_key: str,
    model: str,
    program: List[Dict[str, Any]],
    description: str,
    *,
    units: str = "mm",
    engineering: bool = True,
    print_profile=None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    thinking_level: Optional[str] = None,
    part_layout: str = "fused",
    system_prompt: Optional[str] = None,
) -> GenerationResult:
    """Generate ONLY the next operation(s) to append to an existing program.

    Returns a :class:`GenerationResult` whose ``program`` holds just the new ops.
    The combined (existing + new) program is validated so references and unique
    names are guaranteed. One automatic repair attempt on failure.

    ``system_prompt`` replaces the standing instructions only; the program built
    so far is always appended, so a custom prompt cannot cost a step its context.
    """
    program = list(program or [])
    system = prompts.step_system_prompt(
        units, program, engineering=engineering, print_profile=print_profile,
        part_layout=part_layout, base=system_prompt or ""
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": description},
    ]
    raw = provider.chat(
        ChatRequest(messages=messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                    json_schema=_step_schema(),
                    json_schema_strict=schema.json_schema(strict=True)),
        api_key,
    )
    try:
        new_ops, notes = _extract_new_ops(raw, program)
        return GenerationResult(mode="engineering", raw=raw, program=new_ops,
                                messages=messages, notes=notes, **_meta(raw))
    except (LLMError, schema.SchemaError) as first_error:
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": prompts.repair_prompt(str(first_error))},
        ]
        raw2 = provider.chat(
            ChatRequest(messages=repair_messages, model=model, temperature=temperature,
                        max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                        json_schema=_step_schema(),
                        json_schema_strict=schema.json_schema(strict=True)),
            api_key,
        )
        try:
            new_ops, notes = _extract_new_ops(raw2, program)
        except (LLMError, schema.SchemaError) as second_error:
            second_error.raw_reply = raw2
            raise
        return GenerationResult(
            mode="engineering", raw=raw2, program=new_ops, repaired=True,
            messages=messages, notes=notes, **_meta(raw2)
        )


def _step_schema() -> Dict[str, Any]:
    """Schema for a step reply - the same ``{"operations": [...]}`` envelope.

    A step returns only the ops to append, but the envelope is identical, so
    grammar-constrained local models get the same guarantee here.
    """
    return schema.json_schema()


def _extract_new_ops(raw: str, program: List[Dict[str, Any]]):
    """Parse a step reply into appendable ops. Returns ``(ops, notes)``.

    A step that reuses an existing object name is renamed rather than rejected:
    the append-only protocol makes the collision inevitable the moment the user
    asks to change something that already exists, and the correction is always
    the same, so spending a repair round on it helps nobody.
    """
    data = extract_json(raw)
    new_ops = data.get("operations") if isinstance(data, dict) else data
    if not isinstance(new_ops, list) or not new_ops:
        raise schema.SchemaError("Step reply did not contain an 'operations' array.")
    taken = [op["name"] for op in program
             if isinstance(op.get("name"), str)
             and schema.OPERATIONS.get(op.get("op"), {}).get("defines")]
    new_ops, notes = schema.dedupe_names(new_ops, taken)
    # Validate the combined program so references resolve.
    schema.validate_program({"operations": program + new_ops})
    return new_ops, notes
