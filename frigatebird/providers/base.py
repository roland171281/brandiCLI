"""Provider-Abstraktion: das Interface, das ALLE LLM-Anbieter erfüllen.

===========================================================================
WARUM GIBT ES DIESE ABSTRAKTION?
===========================================================================
Der Agent-Loop (agent.py) soll sich NICHT darum kümmern müssen, wie
man mit OpenAI, Anthropic oder OpenRouter redet. Er ruft einfach:

    reply = provider.chat(messages, tools=...)

und bekommt immer die GLEICHE Antwort-Struktur zurück. Alles provider-
spezifische (URLs, Header, Antwort-Formate) steckt in den Unterklassen.

Das nennt man auch "Hexagonal Architecture" oder Ports & Adapters:
  - Der Agent definiert ein Port-Interface (diese Datei).
  - Jeder Provider ist ein Adapter, der dieses Interface implementiert.
"""

from __future__ import annotations

import abc

from frigatebird.messages import ToolCall


class Provider(abc.ABC):
    """Basisklasse für alle Provider (API-Anbindungen).

    Beispiel für einen konkreten Provider: AnthropicProvider unten.
    Jede Unterklasse MUSS chat() implementieren; list_models() ist optional.
    """

    name: str = "abstract"          # Name für /status und Fehlermeldungen
    default_model: str = ""         # Modell, das verwendet wird, wenn nichts angegeben

    def __init__(self, model: str | None = None):
        """Speichert das gewünschte Modell.

        Wenn der Konstruktor eines konkreten Providers z.B. für
        OpenRouter eine Standard-Modell-ID setzt (default_model), kann
        man hier trotzdem ein Modell überschreiben - so funktioniert das
        /model-Kommando im CLI.
        """
        self.model = model or self.default_model

    @abc.abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> dict:
        """Eine (nicht-streamende) Chat-Completion durchführen.

        Eingabe:
          messages: Konversation im INTERNEN Format (siehe messages.py)
          tools:    OpenAI-Style Tool-Schemas
                    [{"type": "function", "function": {...}}, ...]
          model:    optionales Überschreiben des Standard-Modells

        Rückgabe:  Eine assistant-Nachricht im INTERNEN Format, evtl.
                   mit "tool_calls".
        Wirft:     ProviderError bei HTTP/API-Fehlern.
        """

    def list_models(self) -> list[str]:
        """Optional: verfügbare Modelle von der API abfragen.

        Beispiel-Implementierung in OpenAICompatProvider; für APIs, die
        so einen Endpoint nicht haben, bleibt die Liste leer.
        """
        return []

    @staticmethod
    def _extract_tool_calls(raw_calls: list) -> list[ToolCall]:
        """Hilfsfunktion für Unterklassen: OpenAI-Tool-Calls -> ToolCall-Objekte."""
        return [ToolCall.from_dict(c) for c in raw_calls or []]


class ProviderError(RuntimeError):
    """Ausnahme, wenn ein API-Aufruf fehlschlägt.

    Sie wird NICHT innerhalb des Agent-Loops behandelt (dort kümmern wir
    uns um Tool-Fehler), sondern im CLI - wo sie dann als rote Meldung
    im Terminal landet. So kann der User entscheiden, ob er es nochmal
    versucht oder die Konfiguration ändert.
    """

    def __init__(self, provider: str, status: int | None, body: str):
        where = f"{provider} HTTP {status}" if status else provider
        # body auf 500 Zeichen kürzen, damit die Meldung lesbar bleibt
        super().__init__(f"{where}: {body[:500]}")
