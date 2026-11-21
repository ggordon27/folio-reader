"""Gemini Interactions API client using httpx with automatic model fallback and SSE streaming."""

from __future__ import annotations

import json
from typing import Iterator, Tuple

import httpx

from .config import GEMINI_API_BASE
from .db import resolve_gemini_api_key


def _extract_text_from_interaction(resp_json: dict) -> str:
    output_text = resp_json.get("output_text") or ""
    if output_text:
        return output_text
    texts = []
    for step in resp_json.get("steps") or []:
        if step.get("type") == "model_output":
            for part in step.get("content") or []:
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
    return "".join(texts).strip()


def _model_fallback_order(primary_model: str) -> list:
    primary = primary_model or "gemini-4-hydrogen"
    order = [primary]
    for m_cand in ("gemini-4-argon", "gemini-3.5-flash-lite", "gemini-4-hydrogen", "gemini-4-carbon"):
        if m_cand not in order:
            order.append(m_cand)
    return order


def call_gemini_interactions_api(
    prompt: str,
    system_instruction: str = "",
    model: str = "gemini-4-hydrogen",
    previous_interaction_id: str = "",
) -> dict:
    """
    Calls the Gemini Interactions API (`POST https://generativelanguage.googleapis.com/v1/interactions`)
    with automatic fallback on 429/5xx demand spikes.
    """
    api_key = resolve_gemini_api_key()
    if not api_key:
        raise RuntimeError("MISSING_API_KEY")

    fallback_order = _model_fallback_order(model)
    last_err = None
    used_model = fallback_order[0]
    resp_json = None

    with httpx.Client(timeout=90.0) as client:
        for candidate_model in fallback_order:
            body = {"model": candidate_model, "input": prompt}
            if system_instruction:
                body["system_instruction"] = system_instruction
            if previous_interaction_id:
                body["previous_interaction_id"] = previous_interaction_id

            resp = client.post(
                GEMINI_API_BASE,
                json=body,
                headers={"x-goog-api-key": api_key},
            )
            if resp.status_code == 200:
                resp_json = resp.json()
                used_model = candidate_model
                break
            last_err = f"Gemini API error ({resp.status_code}): {resp.text}"
            if resp.status_code in (429, 500, 502, 503, 504):
                continue
            raise RuntimeError(last_err)

    if resp_json is None:
        raise RuntimeError(last_err or "Gemini API call failed")

    return {
        "id": resp_json.get("id", ""),
        "status": resp_json.get("status", "completed"),
        "output_text": _extract_text_from_interaction(resp_json),
        "model": used_model,
        "usage": resp_json.get("usage", {}),
    }


def _extract_stream_delta(event_obj: dict) -> Tuple[str, str]:
    """Extracts (delta_text, interaction_id) from a Gemini Interactions SSE JSON payload."""
    inter_id = event_obj.get("id") or ""
    if not inter_id and isinstance(event_obj.get("interaction"), dict):
        inter_id = event_obj["interaction"].get("id") or ""

    # Common Interactions streaming shapes:
    # 1) {"delta": {"type": "text", "text": "..."}} or {"delta": "..."}
    delta = event_obj.get("delta")
    if isinstance(delta, str) and delta:
        return delta, inter_id
    if isinstance(delta, dict):
        if delta.get("text"):
            return str(delta["text"]), inter_id
        if isinstance(delta.get("content"), list):
            parts = [p.get("text", "") for p in delta["content"] if isinstance(p, dict) and p.get("text")]
            if parts:
                return "".join(parts), inter_id

    # 2) {"output_text": "..."} or {"steps": [...]}
    txt = _extract_text_from_interaction(event_obj)
    if txt:
        return txt, inter_id
    return "", inter_id


def stream_gemini_interactions_api(
    prompt: str,
    system_instruction: str = "",
    model: str = "gemini-4-hydrogen",
    previous_interaction_id: str = "",
) -> Iterator[Tuple[str, object]]:
    """
    Streams response tokens from Gemini Interactions API.
    Yields ("delta", text_chunk) and finally ("done", result_dict).
    Falls back cleanly to non-streaming if the endpoint rejects stream=True or returns JSON.
    """
    api_key = resolve_gemini_api_key()
    if not api_key:
        raise RuntimeError("MISSING_API_KEY")

    fallback_order = _model_fallback_order(model)
    last_err = None

    with httpx.Client(timeout=120.0) as client:
        for candidate_model in fallback_order:
            body = {"model": candidate_model, "input": prompt, "stream": True}
            if system_instruction:
                body["system_instruction"] = system_instruction
            if previous_interaction_id:
                body["previous_interaction_id"] = previous_interaction_id

            try:
                with client.stream(
                    "POST",
                    GEMINI_API_BASE,
                    json=body,
                    headers={"x-goog-api-key": api_key, "Accept": "text/event-stream"},
                ) as resp:
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_err = f"Gemini API error ({resp.status_code}): {resp.read().decode('utf-8', errors='replace')}"
                        continue
                    if resp.status_code == 400:
                        # Endpoint may not accept stream=True on this model; fall back to standard call
                        res = call_gemini_interactions_api(prompt, system_instruction, candidate_model, previous_interaction_id)
                        if res["output_text"]:
                            yield ("delta", res["output_text"])
                        yield ("done", res)
                        return
                    if resp.status_code >= 400:
                        err_text = resp.read().decode("utf-8", errors="replace")
                        raise RuntimeError(f"Gemini API error ({resp.status_code}): {err_text}")

                    ctype = (resp.headers.get("content-type") or "").lower()
                    if "text/event-stream" not in ctype:
                        raw_json = json.loads(resp.read().decode("utf-8"))
                        full_text = _extract_text_from_interaction(raw_json)
                        if full_text:
                            yield ("delta", full_text)
                        yield (
                            "done",
                            {
                                "id": raw_json.get("id", ""),
                                "status": raw_json.get("status", "completed"),
                                "output_text": full_text,
                                "model": candidate_model,
                                "usage": raw_json.get("usage", {}),
                            },
                        )
                        return

                    collected = []
                    inter_id = ""
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload_str = line[5:].strip()
                        if not payload_str or payload_str == "[DONE]":
                            continue
                        try:
                            ev = json.loads(payload_str)
                        except Exception:
                            continue
                        chunk, found_id = _extract_stream_delta(ev)
                        if found_id:
                            inter_id = found_id
                        if chunk:
                            # Avoid duplicating full text if final event repeats accumulated output
                            current_full = "".join(collected)
                            if current_full and chunk == current_full:
                                continue
                            collected.append(chunk)
                            yield ("delta", chunk)

                    full_text = "".join(collected).strip()
                    yield (
                        "done",
                        {
                            "id": inter_id,
                            "status": "completed",
                            "output_text": full_text,
                            "model": candidate_model,
                            "usage": {},
                        },
                    )
                    return
            except RuntimeError:
                raise
            except Exception as exc:
                last_err = str(exc)
                continue

    raise RuntimeError(last_err or "Gemini API streaming call failed")
