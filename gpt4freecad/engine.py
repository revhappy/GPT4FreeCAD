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
from .llm import ChatRequest, Provider, extract_json
from .llm.base import LLMError


@dataclass
class GenerationResult:
    mode: str
    raw: str
    program: Optional[List[Dict[str, Any]]] = None  # structured mode
    code: Optional[str] = None                       # python mode
    repaired: bool = False
    messages: List[Dict[str, str]] = field(default_factory=list)


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
    thinking_level: str = None,
    print_profile=None,
    part_layout: str = "fused",
) -> GenerationResult:
    """Run one generation. Raises LLMError / SchemaError on unrecoverable failure."""
    history = list(history or [])

    if mode == "python":
        return _generate_python(
            provider, api_key, model, description, history,
            temperature, max_tokens, thinking_level,
        )
    return _generate_structured(
        provider, api_key, model, description, history,
        units, temperature, max_tokens, thinking_level,
        engineering=(mode == "engineering"), print_profile=print_profile,
        part_layout=part_layout,
    )


def _generate_python(provider, api_key, model, description, history,
                     temperature, max_tokens, thinking_level):
    messages = [{"role": "system", "content": prompts.PYTHON_SYSTEM_PROMPT}]
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
    return GenerationResult(mode="python", raw=raw, code=code, messages=messages)


def _generate_structured(
    provider, api_key, model, description, history,
    units, temperature, max_tokens, thinking_level,
    engineering=False, print_profile=None, part_layout="fused",
):
    system = prompts.system_prompt(
        units, engineering=engineering, print_profile=print_profile,
        part_layout=part_layout)
    messages = [{"role": "system", "content": system}]
    messages += history
    messages.append({"role": "user", "content": description})

    raw = provider.chat(
        ChatRequest(messages=messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                    json_schema=schema.json_schema()),
        api_key,
    )

    try:
        program = schema.validate_program(extract_json(raw))
        return GenerationResult(mode="structured", raw=raw, program=program, messages=messages)
    except (LLMError, schema.SchemaError) as first_error:
        # One automatic repair attempt: show the model its own output + the error.
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": prompts.repair_prompt(str(first_error))},
        ]
        raw2 = provider.chat(
            ChatRequest(messages=repair_messages, model=model, temperature=temperature,
                        max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                        json_schema=schema.json_schema()),
            api_key,
        )
        program = schema.validate_program(extract_json(raw2))  # may raise; let it
        return GenerationResult(
            mode="structured", raw=raw2, program=program, repaired=True, messages=messages
        )


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
    thinking_level: str = None,
    part_layout: str = "fused",
) -> GenerationResult:
    """Generate ONLY the next operation(s) to append to an existing program.

    Returns a :class:`GenerationResult` whose ``program`` holds just the new ops.
    The combined (existing + new) program is validated so references and unique
    names are guaranteed. One automatic repair attempt on failure.
    """
    program = list(program or [])
    system = prompts.step_system_prompt(
        units, program, engineering=engineering, print_profile=print_profile,
        part_layout=part_layout
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": description},
    ]
    raw = provider.chat(
        ChatRequest(messages=messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                    json_schema=_step_schema()),
        api_key,
    )
    try:
        new_ops = _extract_new_ops(raw, program)
        return GenerationResult(mode="engineering", raw=raw, program=new_ops, messages=messages)
    except (LLMError, schema.SchemaError) as first_error:
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": prompts.repair_prompt(str(first_error))},
        ]
        raw2 = provider.chat(
            ChatRequest(messages=repair_messages, model=model, temperature=temperature,
                        max_tokens=max_tokens, json_mode=True, thinking_level=thinking_level,
                        json_schema=_step_schema()),
            api_key,
        )
        new_ops = _extract_new_ops(raw2, program)
        return GenerationResult(
            mode="engineering", raw=raw2, program=new_ops, repaired=True, messages=messages
        )


def _step_schema() -> Dict[str, Any]:
    """Schema for a step reply - the same ``{"operations": [...]}`` envelope.

    A step returns only the ops to append, but the envelope is identical, so
    grammar-constrained local models get the same guarantee here.
    """
    return schema.json_schema()


def _extract_new_ops(raw: str, program: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    data = extract_json(raw)
    new_ops = data.get("operations") if isinstance(data, dict) else data
    if not isinstance(new_ops, list) or not new_ops:
        raise schema.SchemaError("Step reply did not contain an 'operations' array.")
    # Validate the combined program so refs resolve and names stay unique.
    schema.validate_program({"operations": program + new_ops})
    return new_ops
