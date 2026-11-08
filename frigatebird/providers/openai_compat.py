"""OpenAI-kompatibler Provider.

Spricht das /v1/chat/completions-Protokoll. Funktioniert mit:
  - OpenAI            (base_url https://api.openai.com/v1)
  - Ollama            (base_url http://localhost:11434/v1, api_key "ollama")
  - OpenRouter, Groq, LM Studio, vLLM, ... alles OpenAI-kompatible

WICHTIG fürs Lernen: Wir benutzen absichtlich httpx direkt statt der
offiziellen OpenAI-SDK. So siehst du genau, was über die Leitung geht:
POST-Request mit JSON-Body -> JSON-Antwort -> parsen. Mehr ist es nicht.
"""

from __future__ import annotations

import os

import httpx

from frigatebird.messages import ToolCall
from frigatebird.providers.base import Provider, ProviderError


class OpenAICompatProvider(Provider):
    """Der generische Adapter für alle OpenAI-Dialekt-APIs."""

    name = "openai-compat"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ):
        # --- Konfiguration mit Fallback-Kette: Parameter > Env > Default ---
        # Das Muster unten siehst du überall wieder:
        #   "was der Aufrufer sagt" schlägt "was die Umgebung sagt".
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.default_model = model or os.environ.get("FRIGATEBIRD_MODEL", "gpt-4o-mini")
        super().__init__(model)

        # httpx.Client = wiederverwendbare HTTP-Verbindung.
        # base_url: alle relativen Pfade ("/chat/completions") landen
        #           automatisch auf dieser Domain.
        # header:   Authentifizierung ist für den OpenAI-Stil IMMER ein
        #           "Bearer <key>" im Authorization-Header.
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
        )

    def chat(self, messages, tools=None, model=None):
        """Eine Chat-Completion im OpenAI-Format durchführen.

        ANFRAGE  : POST {base_url}/chat/completions
        ANTWORT  : {"choices": [{"message": {"role","content","tool_calls"}}], "usage": ...}
        """
        # --- 1) Request-Payload bauen --------------------------------------
        payload: dict = {"messages": messages, "model": model or self.model}
        if tools:
            # Nur mitschicken, wenn es Tools gibt - sonst reagieren manche
            # Server seltsam.
            payload["tools"] = tools

        # --- 2) Abschicken und Fehler behandeln ----------------------------
        r = self._client.post("/chat/completions", json=payload)
        if r.status_code >= 400:
            # Bei 4xx/5xx: ein einheitlicher ProviderError, damit das CLI
            # (und nicht der Agent-Loop) diese Meldung anzeigen kann.
            raise ProviderError(self.name, r.status_code, r.text)
        data = r.json()

        # --- 3) Antwort ins interne Format übersetzen ----------------------
        # OpenAI liefert eine Liste "choices" (wir bitten immer nur nach 1),
        # und die eigentliche Nachricht steckt in choice["message"].
        choice = data["choices"][0]
        msg = choice["message"]
        return {
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            # Nutzungsdaten (Token-Zahlen) mitnehmen, damit die CLI sie
            # anzeigen kann. Der Unterstrich "_" markiert das Feld als
            # "interne Meta-Info", die NICHT zurück in die API geht.
            "_usage": data.get("usage"),
        }

    def list_models(self):
        """Verfügbare Modelle abfragen (OpenAI-Standard-Endpoint /models)."""
        r = self._client.get("/models")
        if r.status_code >= 400:
            raise ProviderError(self.name, r.status_code, r.text)
        return [m["id"] for m in r.json().get("data", [])]


class OllamaProvider(OpenAICompatProvider):
    """Ollama (lokale LLMs) exponiert einen OpenAI-kompatiblen Endpoint.

    Unterklassifizierung reicht - wir ändern nur die Standardwerte.
    So siehst du, WARUM die Abstraktion lohnt: ein neuer Provider ist
    oft nur eine Zeile Konfiguration.
    """

    name = "ollama"

    def __init__(self, model: str | None = None):
        super().__init__(
            base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434/v1"),
            api_key="ollama",  # Ollama ignoriert den Key, aber der Header muss existieren
            model=model or "llama3.2",
        )


class OpenRouterProvider(OpenAICompatProvider):
    """OpenRouter (https://openrouter.ai) - eine API für hunderte Modelle.

    Modell-IDs sind namespaced: "openai/gpt-4o-mini",
    "anthropic/claude-3.5-sonnet", "meta-llama/llama-3.3-70b-instruct:free", ...
    """

    name = "openrouter"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        super().__init__(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            model=model or "z-ai/glm-5.3-flash",  # Default: das Modell des bauleitenden Agents :)
        )
        # Optional, aber von OpenRouter empfohlen: App-Attribution.
        # (OpenRouter zeigt dann im Dashboard, welche App die Anfragen gestellt hat.)
        self._client.headers["HTTP-Referer"] = "https://github.com/frigatebird"
        self._client.headers["X-Title"] = "frigatebird"
