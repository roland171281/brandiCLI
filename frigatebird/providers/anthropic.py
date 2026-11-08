"""Anthropic-Provider - der Musteradapter für "fremde" Protokolle.

Die Anthropic-API unterscheidet sich von OpenAI in Punkten, die sich
besonders gut zum Lernen eignen, weil sie zeigen, WARUM wir ein internes
Format brauchen:

  1. Der System-Prompt ist ein TOPIK-PARAMETER der Anfrage, keine
     Nachricht in der Liste.
  2. Tool-Calls sind CONTENT-BLOCKS ("tool_use") INNERHALB der
     Assistant-Antwort, kein separates Feld.
  3. Tool-ERGEBNISSE sind USER-Nachrichten, die "tool_result"-Blocks
     enthalten (nicht wie bei OpenAI eine eigene "tool"-Rolle).
  4. Tool-Schemas heißen "input_schema" statt "parameters" und haben
     keinen "type": "function"-Wrapper.

DIESE DATEI MACHT NUR EINEN DING: Übersetzen.
  _to_native()      : internes Format -> Anthropic-Format (für die Anfrage)
  _to_internal()    : Anthropic-Format -> internes Format (für die Antwort)
"""

from __future__ import annotations

import os

import httpx

from frigatebird.messages import ToolCall
from frigatebird.providers.base import Provider, ProviderError

# --- intern (OpenAI-Style) -> native (Anthropic) ---------------------------


def _to_native(messages: list[dict]) -> tuple[str, list[dict]]:
    """Übersetzt die komplette Konversation in das Anthropic-Format.

    Rückgabe: (system_text, liste_von_native_nachrichten)
    """
    system_parts: list[str] = []
    native: list[dict] = []

    for msg in messages:
        role, content = msg["role"], msg["content"]

        if role == "system":
            # UMWANDLUNG 1: System-Nachricht wird AUSGESCHNITTEN und als
            # separater Parameter übergeben (kein Teil der messages-Liste).
            system_parts.append(content)
            continue

        if role == "tool":
            # UMWANDLUNG 3: Ein OpenAI-"tool"-Ergebnis wird zu einer
            # USER-Nachricht mit einem tool_result-Block.
            native.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],  # Verknüpfung zum Original-Call
                    "content": msg["content"],
                }],
            })
            continue

        if role == "assistant" and msg.get("tool_calls"):
            # UMWANDLUNG 2: OpenAI-Tool-Calls werden zu tool_use-CONTENT-
            # BLOCKS innerhalb der Assistant-Nachricht.
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                import json
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args})
            native.append({"role": "assistant", "content": blocks})
            continue

        # "user" (und leere "assistant") können unverändert bleiben
        native.append({"role": role, "content": content})

    return "\n\n".join(system_parts), native


def _to_native_tools(tools: list[dict] | None) -> list[dict]:
    """Übersetzt die Tool-Schemas: "parameters" wird zu "input_schema".

    UMWANDLUNG 4: Anthropic kennt KEINEN "type": "function"-Wrapper und
    einen anderen Namen für das Parameter-Schema.
    """
    if not tools:
        return []
    out = []
    for t in tools:
        fn = t["function"]  # {"name","description","parameters"} auspacken
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


# --- native (Anthropic) -> intern (OpenAI-Style) ----------------------------


def _to_internal(resp: dict) -> dict:
    """Übersetzt eine Anthropic-Antwort zurück in unser internes Format.

    Die Anthropic-Antwort sieht ungefähr so aus:
      {"content": [
         {"type": "text", "text": "..."},
         {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
       ],
       "usage": {...}}
    Wir "abflachen" das zu einer einzigen Assistant-Nachricht im
    OpenAI-Style (content + tool_calls), damit der Agent-Loop sich nicht
    um Anthropic-Blocks kümmern muss.
    """
    content, tool_calls = "", []
    for block in resp.get("content", []):
        if block["type"] == "text":
            content += block["text"]
        elif block["type"] == "tool_use":
            import json
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    # Anthropic liefert "input" als DICT, wir brauchen
                    # wieder einen JSON-String (unser internes Format)
                    "arguments": json.dumps(block.get("input", {})),
                },
            })
    return {"role": "assistant", "content": content, "tool_calls": tool_calls,
            "_usage": resp.get("usage")}


# --- der eigentliche Provider -------------------------------------------------


class AnthropicProvider(Provider):
    name = "anthropic"
    default_model = "claude-sonnet-4-5"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        super().__init__(model)
        # Anthropic-Auth läuft über zwei Header (NICHT über Bearer!):
        #   x-api-key          -> der API-Key selbst
        #   anthropic-version  -> Datum der gewünschten API-Version
        self._client = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=timeout,
        )

    def chat(self, messages, tools=None, model=None):
        """Ein Anthropic-/v1/messages-Aufruf: übersetzen -> POST -> zurückübersetzen."""
        system, native = _to_native(messages)
        payload: dict = {
            "model": model or self.model,
            "max_tokens": 8192,  # Pflichtfeld bei Anthropic (anders als bei OpenAI!)
            "messages": native,
        }
        if system:
            payload["system"] = system
        native_tools = _to_native_tools(tools)
        if native_tools:
            payload["tools"] = native_tools

        r = self._client.post("/v1/messages", json=payload)
        if r.status_code >= 400:
            raise ProviderError(self.name, r.status_code, r.text)
        return _to_internal(r.json())

    def list_models(self):
        r = self._client.get("/v1/models")
        if r.status_code >= 400:
            raise ProviderError(self.name, r.status_code, r.text)
        return [m["id"] for m in r.json().get("data", [])]
