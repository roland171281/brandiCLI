"""Das interne Nachrichtenformat von frigatebird.

===========================================================================
WARUM GIBT ES DIESE DATEI ÜBERHAUPT?
===========================================================================
Jeder LLM-Anbieter spricht seinen eigenen "Dialekt" (Anthropic nutzt
Content-Blocks wie "tool_use"/"tool_result", OpenAI nutzt "tool_calls").
Damit unser Agent-Loop und die Sitzungsverwaltung NICHT von einem bestimmten
Anbieter abhängen, definieren wir hier EIN internes Format, an das sich
alle Provider halten.

Wir haben uns für den OpenAI-Stil entschieden, weil er am verbreitetsten
ist und auch bei OpenRouter & Ollama funktioniert.

Das Format ist einfach eine Liste von Dictionaries (JSON-Objekten):
"""

from __future__ import annotations  # MUSS immer ganz oben stehen (direkt nach dem Docstring)

import json
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Vier Arten von Nachrichten (die "role" unterscheidet sie):
# ---------------------------------------------------------------------------

# 1. {"role": "system",   "content": "Du bist ein hilfreicher Assistent."}
#    -> Die Instruktionen des Entwicklers. Das Modell "sieht" sie zuerst,
#       der User aber nie direkt in der UI. Darum geht alles andere.

# 2. {"role": "user",     "content": "Was ist 1+1?"}
#    -> Eine Eingabe des Menschen (bzw. was immer wir dem Modell als
#       User-Eingabe unterjubeln).

# 3. {"role": "assistant","content": "...",
#     "tool_calls": [...]}                          <- optional
#    -> Die Antwort des Modells. Sie kann TEXT sein (finale Antwort) ODER
#       einen oder mehrere Wünsche, Tools aufzurufen ("tool_calls"),
#       ODER beides gleichzeitig.

# 4. {"role": "tool",     "content": "...", "tool_call_id": "call_1"}
#    -> Das ERGEBNIS eines Tool-Aufrufs. Muss nach jedem Tool-Call kommen,
#       sonst ist die Konversation für die API "kaputt" (undefiniert).

# ---------------------------------------------------------------------------
# So sieht ein Tool-Call in diesem Format aus:
# ---------------------------------------------------------------------------
TOOL_CALL_BEISPIEL = {
    "id": "call_1",                     # eindeutige ID, muss beim Ergebnis wiederkehren
    "type": "function",                 # OpenAI-Konvention, immer "function"
    "function": {
        "name": "bash",                 # WELCHES Tool ausgeführt werden soll
        "arguments": '{"command": "ls"}'  # Argumente als JSON-STRING, nicht als Dict!
    },
}

# Achtung: "arguments" ist absichtlich ein String. Das Modell generiert Text,
# und JSON-in-String ist der kleinste gemeinsame Nenner aller Anbieter.
# Unsere ToolCall-Klasse kann ihn mit .parsed_args() in ein Dict umwandeln.


@dataclass
class ToolCall:
    """Ein einzelner, vom Modell angeforderter Tool-Aufruf.

    Wenn das Modell z.B. `ls` ausführen will, erzeugen wir daraus:
        ToolCall(id="call_1", name="bash", arguments='{"command": "ls"}')
    """

    id: str          # eindeutige Kennung des Calls (vom Modell generiert)
    name: str        # Name des Tools, das ausgeführt werden soll
    arguments: str   # roher JSON-String, exakt wie das Modell ihn ausgegeben hat

    def parsed_args(self) -> dict:
        """Parst die Argumente in ein Dict.

        Defensiv gebaut, denn Modelle sind manchmal schluderig:
        - leere oder defekte JSON-Strings -> leeres Dict statt Crash
        - "true" oder "[...]" (kein Objekt) -> ebenfalls leeres Dict,
          denn unsere Tools erwarten immer ein Dict von Argumenten
        """
        try:
            args = json.loads(self.arguments or "{}")
            return args if isinstance(args, dict) else {}
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict:
        """Wandelt zurück in das OpenAI-Format für die API-Anfrage.

        Genau dieses Dict landet in der assistant-Nachricht, die wir
        zurück an die API schicken (damit das Modell "weiß", dass es
        diesen Call gemacht hat).
        """
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolCall":
        """Baut ein ToolCall-Objekt aus einer API-Antwort (OpenAI-Format).

        Bekommt genau die Struktur aus TOOL_CALL_BEISPIEL oben.
        """
        return cls(
            id=d.get("id", "call_unknown"),
            name=d["function"]["name"],
            arguments=d["function"].get("arguments", "{}"),
        )


def user(content: str) -> dict:
    """Hilfsfunktion: erzeugt eine user-Nachricht (Variante 2 oben)."""
    return {"role": "user", "content": content}


def system(content: str) -> dict:
    """Hilfsfunktion: erzeugt eine system-Nachricht (Variante 1 oben)."""
    return {"role": "system", "content": content}


def assistant(content: str | None, tool_calls: list[ToolCall] | None = None) -> dict:
    """Hilfsfunktion: erzeugt eine assistant-Nachricht (Variante 3 oben).

    content darf leer sein, wenn das Modell NUR Tools aufrufen will.
    """
    msg: dict = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = [tc.to_dict() for tc in tool_calls]
    return msg


def tool_result(call: ToolCall, output: str) -> dict:
    """Hilfsfunktion: erzeugt das Ergebnis eines Calls (Variante 4 oben).

    WICHTIG: tool_call_id verknüpft das Ergebnis mit dem Original-Call.
    Ohne diese Verknüpfung könnte das Modell nicht zuordnen, welches
    Ergebnis zu welchem Aufruf gehört (wenn es z.B. drei parallel ruft).
    """
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": output,
    }
