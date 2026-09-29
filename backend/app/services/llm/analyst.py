"""
The AI Satellite Analyst.

Architecture:

    question
        ↓
      LLM
        ↓
    tool calls
        ↓
 REAL backend data
        ↓
      LLM
        ↓
     prose

Supported LLM providers:
    - Anthropic
    - Gemini
    - Groq

Groq uses the OpenAI-compatible Chat Completions API.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Job
from app.services.llm.tools import (
    REGISTRY,
    anthropic_schema,
    call_tool,
)

log = get_logger(__name__)

MAX_TOOL_ROUNDS = 4


SYSTEM_PROMPT = """You are the PixelForge Satellite Analyst. You help a remote-sensing \
analyst interpret the results of a super-resolution job.

ABSOLUTE RULES:

1. NEVER state a numeric value unless it appeared in a tool result in this conversation.
   Do not estimate, interpolate, round from memory, or recall numbers from training data.

2. If a metric is null or a tool reports it unavailable, say plainly that it could not be
   computed and give the reason the tool provided. Never substitute a plausible number.

3. Super-resolved detail is RECONSTRUCTED, not observed. When discussing visible features,
   say so.

4. Uncertainty values in PixelForge are relative model-disagreement indicators, not
   calibrated probabilities. Never describe them as "confidence" percentages without
   that qualification.

5. Call tools to get facts. Prefer several small tool calls over guessing.

6. Be concise and specific. Write for a professional analyst, not a marketing page.

7. Never invent information that is not present in the tool results.

If asked something the tools cannot answer, say so directly.
"""


# ---------------------------------------------------------------------------
# DETERMINISTIC FALLBACK
# ---------------------------------------------------------------------------


def _template_answer(
    question: str,
    results: list[dict],
) -> str:
    """
    Deterministic fallback used when no LLM key is configured.
    """

    q = question.lower()

    merged: dict[str, Any] = {}

    for result in results:
        merged.update(result.get("result", {}))

    lines: list[str] = []

    # Model runs
    if "runs" in merged and isinstance(
        merged["runs"],
        list,
    ):
        for run in merged["runs"]:
            if isinstance(run, dict) and run.get("model_key"):
                gsd = run.get("output_gsd_m")

                line = (
                    f"- {run.get('display_name', run['model_key'])}: "
                    f"{run.get('status', 'n/a')}"
                )

                if gsd:
                    line += f", output {gsd} m"

                if run.get("runtime_s"):
                    line += f", {run['runtime_s']} s"

                lines.append(line)

    # Metrics
    if "metrics_by_run" in merged:
        for rid, metrics in merged["metrics_by_run"].items():
            if metrics.get("full_reference") is None:
                lines.append(
                    f"- {metrics.get('model_key', rid)}: "
                    f"full-reference metrics unavailable — "
                    f"{metrics.get('full_reference_unavailable_reason', '')}"
                )

            for name, value in (metrics.get("no_reference") or {}).items():
                lines.append(
                    f"- {metrics.get('model_key', rid)} " f"{name}: {value['value']}"
                )

    # Generic availability response
    if merged.get("available") is False and merged.get("message"):
        lines.append(f"- {merged['message']}")

    # Generic explanation
    if merged.get("explanation"):
        lines.append(merged["explanation"])

    body = "\n".join(lines) if lines else "No data was returned for that question."

    return (
        f"{body}\n\n"
        "_The language model is not configured, so this is a direct "
        "readout of the underlying data rather than a written explanation. "
        "Set `LLM_API_KEY` or `GROQ_API_KEY` in your .env to enable full answers._"
    )


# ---------------------------------------------------------------------------
# NUMERIC PROVENANCE
# ---------------------------------------------------------------------------

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def check_numeric_provenance(
    answer: str,
    tool_results: list[dict],
) -> dict:
    """
    Verify that every number in the answer appeared in a tool result.

    This is an audit signal, not a security boundary.
    """

    corpus = json.dumps(
        tool_results,
        default=str,
    )

    corpus_nums = {
        n.rstrip("0").rstrip(".") if "." in n else n for n in _NUM.findall(corpus)
    }

    unverified = []

    for raw in _NUM.findall(answer):
        norm = raw.rstrip("0").rstrip(".") if "." in raw else raw

        if norm in corpus_nums or raw in corpus:
            continue

        # Ignore small integers that are likely
        # list markers or simple counts.
        if "." not in raw and abs(int(raw)) <= 12:
            continue

        unverified.append(raw)

    return {
        "verified": not unverified,
        "unverified_numbers": sorted(set(unverified))[:10],
    }


# ---------------------------------------------------------------------------
# HTTP RETRY LOGIC
# ---------------------------------------------------------------------------

_RETRYABLE = {
    429,
    500,
    502,
    503,
    504,
}


def _post_with_resilience(
    url_for_model,
    headers: dict,
    body: dict,
    models: list[str],
    *,
    model_in_body: bool = False,
) -> Any:
    """
    POST with exponential backoff.

    For Gemini:
        model is part of the URL.

    For Groq:
        model is part of the JSON body.
    """

    last_exc: Exception | None = None

    for model in models:
        for attempt in range(settings.LLM_RETRIES + 1):
            try:
                request_body = dict(body)

                if model_in_body:
                    request_body["model"] = model

                response = httpx.post(
                    url_for_model(model),
                    timeout=settings.LLM_TIMEOUT_S,
                    headers=headers,
                    json=request_body,
                )

            except (
                httpx.TimeoutException,
                httpx.TransportError,
            ) as exc:
                last_exc = exc
                status = None

            else:
                if response.status_code < 400:
                    if model != models[0]:
                        log.warning(
                            "analyst.llm_fallback_used",
                            model=model,
                        )

                    return response

                last_exc = httpx.HTTPStatusError(
                    f"{response.status_code} from {model}",
                    request=response.request,
                    response=response,
                )

                status = response.status_code

            # Non-retryable error.
            if status is not None and status not in _RETRYABLE:
                log.warning(
                    "analyst.llm_model_rejected",
                    model=model,
                    status=status,
                )
                break

            if attempt < settings.LLM_RETRIES:
                time.sleep(min(2**attempt, 8))

        log.warning(
            "analyst.llm_model_exhausted",
            model=model,
            error=str(last_exc)[:200],
        )

    assert last_exc is not None

    raise last_exc


# ---------------------------------------------------------------------------
# ANTHROPIC
# ---------------------------------------------------------------------------


def _call_anthropic(
    messages: list[dict],
    tools: list[dict],
) -> dict:
    response = httpx.post(
        (settings.LLM_BASE_URL or "https://api.anthropic.com") + "/v1/messages",
        timeout=60,
        headers={
            "x-api-key": settings.LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": settings.LLM_MODEL,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "temperature": settings.LLM_TEMPERATURE,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "tools": tools,
        },
    )

    response.raise_for_status()

    return response.json()


# ---------------------------------------------------------------------------
# GEMINI
# ---------------------------------------------------------------------------

_GEMINI_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "description",
    "enum",
    "items",
}


def _gemini_clean(schema: Any) -> Any:
    """
    Gemini accepts an OpenAPI-like subset of JSON Schema.
    """

    if isinstance(schema, dict):
        output = {}

        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                output[key] = {
                    name: _gemini_clean(subschema) for name, subschema in value.items()
                }

            elif key in _GEMINI_SCHEMA_KEYS:
                output[key] = _gemini_clean(value)

        return output

    return schema


def _call_gemini(
    messages: list[dict],
    tools: list[dict],
) -> dict:
    id_to_name: dict[str, str] = {}

    contents: list[dict] = []

    for message in messages:
        content = message["content"]

        if isinstance(content, str):
            contents.append(
                {
                    "role": ("user" if message["role"] == "user" else "model"),
                    "parts": [{"text": content}],
                }
            )

            continue

        parts = []

        for block in content:
            if block.get("type") == "text":
                parts.append(
                    {
                        "text": block.get(
                            "text",
                            "",
                        )
                    }
                )

            elif block.get("type") == "tool_use":
                id_to_name[block["id"]] = block["name"]

                parts.append(
                    {
                        "functionCall": {
                            "name": block["name"],
                            "args": (block.get("input") or {}),
                        }
                    }
                )

            elif block.get("type") == "tool_result":
                parts.append(
                    {
                        "functionResponse": {
                            "name": id_to_name.get(
                                block["tool_use_id"],
                                "tool",
                            ),
                            "response": {"result": block["content"]},
                        }
                    }
                )

        contents.append(
            {
                "role": ("user" if message["role"] == "user" else "model"),
                "parts": parts,
            }
        )

    declarations = []

    for tool in tools:
        declaration = {
            "name": tool["name"],
            "description": tool["description"],
        }

        params = _gemini_clean(tool["input_schema"])

        if params.get("properties"):
            declaration["parameters"] = params

        declarations.append(declaration)

    base = settings.LLM_BASE_URL or "https://generativelanguage.googleapis.com"

    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "tools": [{"functionDeclarations": declarations}],
        "generationConfig": {
            "maxOutputTokens": settings.LLM_MAX_TOKENS,
            "temperature": settings.LLM_TEMPERATURE,
        },
    }

    response = _post_with_resilience(
        lambda model: f"{base}/v1beta/models/{model}:generateContent",
        {
            "x-goog-api-key": settings.LLM_API_KEY,
            "content-type": "application/json",
        },
        body,
        settings.llm_model_chain,
    )

    candidate = (response.json().get("candidates") or [{}])[0]

    blocks = []

    for index, part in enumerate((candidate.get("content") or {}).get("parts", [])):
        if "text" in part:
            blocks.append(
                {
                    "type": "text",
                    "text": part["text"],
                }
            )

        elif "functionCall" in part:
            function_call = part["functionCall"]

            blocks.append(
                {
                    "type": "tool_use",
                    "id": (f"{function_call['name']}" f"-{index}"),
                    "name": function_call["name"],
                    "input": (function_call.get("args") or {}),
                }
            )

    return {"content": blocks}


# ---------------------------------------------------------------------------
# GROQ
# ---------------------------------------------------------------------------


def _groq_tools(
    tools: list[dict],
) -> list[dict]:
    """
    Convert the existing Anthropic-style
    schema into OpenAI/Groq function tools.
    """

    converted = []

    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get(
                        "description",
                        "",
                    ),
                    "parameters": tool.get(
                        "input_schema",
                        {
                            "type": "object",
                            "properties": {},
                        },
                    ),
                },
            }
        )

    return converted


def _groq_messages(
    messages: list[dict],
) -> list[dict]:
    """
    Convert the internal Anthropic-style
    message representation to the
    OpenAI/Groq Chat Completions format.
    """

    converted: list[dict] = []

    # Always send the analyst rules.
    converted.append(
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    )

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        # Ignore duplicate system messages because
        # SYSTEM_PROMPT is already added above.
        if role == "system":
            if content:
                converted.append(
                    {
                        "role": "system",
                        "content": str(content),
                    }
                )

            continue

        # ---------------------------------------------------------------
        # USER
        # ---------------------------------------------------------------

        if role == "user":
            if isinstance(
                content,
                str,
            ):
                converted.append(
                    {
                        "role": "user",
                        "content": content,
                    }
                )

                continue

            if isinstance(
                content,
                list,
            ):
                for block in content:
                    block_type = block.get("type")

                    # Tool result
                    if block_type == "tool_result":
                        converted.append(
                            {
                                "role": "tool",
                                "tool_call_id": block["tool_use_id"],
                                "content": str(
                                    block.get(
                                        "content",
                                        "",
                                    )
                                ),
                            }
                        )

                    # Text inside a user block
                    elif block_type == "text":
                        converted.append(
                            {
                                "role": "user",
                                "content": block.get(
                                    "text",
                                    "",
                                ),
                            }
                        )

                continue

        # ---------------------------------------------------------------
        # ASSISTANT
        # ---------------------------------------------------------------

        if role == "assistant":
            if isinstance(
                content,
                str,
            ):
                converted.append(
                    {
                        "role": "assistant",
                        "content": content,
                    }
                )

                continue

            if isinstance(
                content,
                list,
            ):
                text_parts: list[str] = []
                tool_calls: list[dict] = []

                for block in content:
                    block_type = block.get("type")

                    if block_type == "text":
                        text = block.get(
                            "text",
                            "",
                        )

                        if text:
                            text_parts.append(text)

                    elif block_type == "tool_use":
                        tool_calls.append(
                            {
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block.get("input") or {}),
                                },
                            }
                        )

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": ("\n".join(text_parts) if text_parts else None),
                }

                if tool_calls:
                    assistant_message["tool_calls"] = tool_calls

                converted.append(assistant_message)

    return converted


def _call_groq(
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """
    Call Groq's OpenAI-compatible API.

    The response is converted back into the
    internal Anthropic-shaped format so the
    existing analyst tool loop continues to work.
    """

    base = (settings.LLM_BASE_URL or "https://api.groq.com/openai/v1").rstrip("/")

    if base.endswith("/chat/completions"):
        endpoint = base
    else:
        endpoint = f"{base}/chat/completions"

    api_key = settings.GROQ_API_KEY or settings.LLM_API_KEY

    if not api_key:
        raise RuntimeError(
            "Groq is selected but neither "
            "GROQ_API_KEY nor LLM_API_KEY "
            "is configured."
        )

    body = {
        "messages": _groq_messages(messages),
        "tools": _groq_tools(tools),
        "tool_choice": "auto",
        "max_tokens": settings.LLM_MAX_TOKENS,
        "temperature": settings.LLM_TEMPERATURE,
    }

    response = _post_with_resilience(
        lambda _model: endpoint,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        body,
        settings.llm_model_chain,
        model_in_body=True,
    )

    data = response.json()

    choices = data.get("choices") or [{}]

    choice = choices[0]

    message = choice.get("message") or {}

    blocks: list[dict] = []

    # ---------------------------------------------------------------
    # Normal text response
    # ---------------------------------------------------------------

    text = message.get("content")

    if text:
        blocks.append(
            {
                "type": "text",
                "text": text,
            }
        )

    # ---------------------------------------------------------------
    # Tool calls
    # ---------------------------------------------------------------

    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}

        arguments = function.get(
            "arguments",
            "{}",
        )

        try:
            parsed_arguments = json.loads(arguments)

        except (
            TypeError,
            json.JSONDecodeError,
        ):
            log.warning(
                "analyst.groq_invalid_tool_arguments",
                tool=tool_call.get("id"),
                arguments=str(arguments)[:500],
            )

            parsed_arguments = {}

        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get(
                    "id",
                    function.get(
                        "name",
                        "tool",
                    ),
                ),
                "name": function.get(
                    "name",
                    "",
                ),
                "input": parsed_arguments,
            }
        )

    return {"content": blocks}


# ---------------------------------------------------------------------------
# LLM ROUTER
# ---------------------------------------------------------------------------


def _call_llm(
    messages: list[dict],
    tools: list[dict],
) -> dict:
    if settings.LLM_PROVIDER == "gemini":
        return _call_gemini(
            messages,
            tools,
        )

    if settings.LLM_PROVIDER == "groq":
        return _call_groq(
            messages,
            tools,
        )

    return _call_anthropic(
        messages,
        tools,
    )


# ---------------------------------------------------------------------------
# MAIN ANALYST
# ---------------------------------------------------------------------------


def ask(
    db: DbSession,
    job: Job,
    question: str,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Returns:

        {
            answer,
            tool_calls,
            provenance,
            llm_used
        }
    """

    tool_calls: list[dict] = []

    # ---------------------------------------------------------------
    # NO LLM
    # ---------------------------------------------------------------

    if not settings.llm_enabled:
        picked = _heuristic_tools(question)

        for name, args in picked:
            result = call_tool(
                name,
                db,
                job,
                **args,
            )

            tool_calls.append(
                {
                    "tool": name,
                    "input": args,
                    "result": result,
                }
            )

        return {
            "answer": _template_answer(
                question,
                tool_calls,
            ),
            "tool_calls": tool_calls,
            "llm_used": False,
            "provenance": {
                "verified": True,
                "unverified_numbers": [],
                "note": "Template mode: all values " "are read directly from tools.",
            },
            "degraded_reason": "LLM_NOT_CONFIGURED",
        }

    # ---------------------------------------------------------------
    # LLM MODE
    # ---------------------------------------------------------------

    messages = list(history or [])

    messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    tools = anthropic_schema()

    answer = ""

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            payload = _call_llm(
                messages,
                tools,
            )

            blocks = payload.get(
                "content",
                [],
            )

            # Extract normal text.
            answer = "\n".join(
                block.get(
                    "text",
                    "",
                )
                for block in blocks
                if block.get("type") == "text"
            ).strip()

            # Extract requested tools.
            uses = [block for block in blocks if block.get("type") == "tool_use"]

            # No tool call means the LLM
            # has produced its final answer.
            if not uses:
                break

            # Save assistant tool request.
            messages.append(
                {
                    "role": "assistant",
                    "content": blocks,
                }
            )

            results = []

            for use in uses:
                tool_name = use["name"]

                tool_input = use.get("input") or {}

                out = call_tool(
                    tool_name,
                    db,
                    job,
                    **tool_input,
                )

                tool_calls.append(
                    {
                        "tool": tool_name,
                        "input": tool_input,
                        "result": out,
                    }
                )

                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use["id"],
                        "content": json.dumps(
                            out,
                            default=str,
                        )[:12000],
                    }
                )

            # Feed all tool results back to
            # the model.
            messages.append(
                {
                    "role": "user",
                    "content": results,
                }
            )

    except Exception as exc:
        log.warning(
            "analyst.llm_failed",
            error=str(exc),
        )

        # If the LLM failed before making
        # any tool calls, use deterministic
        # tool selection.
        if not tool_calls:
            for name, args in _heuristic_tools(question):
                tool_calls.append(
                    {
                        "tool": name,
                        "input": args,
                        "result": call_tool(
                            name,
                            db,
                            job,
                            **args,
                        ),
                    }
                )

        return {
            "answer": _template_answer(
                question,
                tool_calls,
            ),
            "tool_calls": tool_calls,
            "llm_used": False,
            "degraded_reason": "LLM_REQUEST_FAILED",
            "provenance": {
                "verified": True,
                "unverified_numbers": [],
            },
        }

    # ---------------------------------------------------------------
    # NUMERIC PROVENANCE CHECK
    # ---------------------------------------------------------------

    provenance = check_numeric_provenance(
        answer,
        tool_calls,
    )

    if not provenance["verified"]:
        log.warning(
            "analyst.unverified_numbers",
            job_id=job.id,
            numbers=provenance["unverified_numbers"],
        )

        answer += (
            "\n\n⚠️ Some figures above "
            "could not be traced to a data "
            "source. Treat them with caution "
            "and check the Data used panel."
        )

    return {
        "answer": answer,
        "tool_calls": tool_calls,
        "llm_used": True,
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# HEURISTIC TOOL SELECTION
# ---------------------------------------------------------------------------


def _heuristic_tools(
    question: str,
) -> list[tuple[str, dict]]:
    q = question.lower()

    picks: list[tuple[str, dict]] = []

    # Model comparison
    if any(
        word in q
        for word in (
            "compare",
            "versus",
            " vs ",
            "better",
            "difference between model",
        )
    ):
        picks.append(
            (
                "compare_models",
                {},
            )
        )

    # Uncertainty
    if any(
        word in q
        for word in (
            "uncertain",
            "confidence",
            "disagree",
            "trust",
            "reliab",
        )
    ):
        picks.append(
            (
                "get_uncertainty",
                {},
            )
        )

    # Metrics
    if any(
        word in q
        for word in (
            "psnr",
            "ssim",
            "sam",
            "rmse",
            "lpips",
            "metric",
            "quality",
            "score",
        )
    ):
        picks.append(
            (
                "get_metrics",
                {},
            )
        )

    # Metric explanations
    for metric in (
        "psnr",
        "ssim",
        "sam",
        "rmse",
        "lpips",
    ):
        if (
            f"what is {metric}" in q
            or f"what does {metric}" in q
            or f"{metric} mean" in q
        ):
            picks.append(
                (
                    "explain_metric",
                    {"metric": metric},
                )
            )

    # Satellite analysis
    if any(
        word in q
        for word in (
            "ndvi",
            "vegetation",
            "water",
            "ndwi",
            "analysis",
            "land cover",
        )
    ):
        picks.append(
            (
                "get_analyses",
                {},
            )
        )

    # Scene metadata
    if any(
        word in q
        for word in (
            "when",
            "acquired",
            "cloud",
            "scene",
            "metadata",
            "date",
        )
    ):
        picks.append(
            (
                "get_scene_metadata",
                {},
            )
        )

    # Models
    if any(
        word in q
        for word in (
            "model",
            "which models",
            "available",
        )
    ):
        picks.append(
            (
                "list_model_runs",
                {},
            )
        )

    # Generic question
    if not picks:
        picks = [
            (
                "get_job_summary",
                {},
            ),
            (
                "list_model_runs",
                {},
            ),
            (
                "get_metrics",
                {},
            ),
        ]

    return picks[:4]
