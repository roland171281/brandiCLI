"""Tool-Grundlage: Definition, Registrierung, Ausführung.

===========================================================================
DAS KONZEPT HINTER "TOOLS" (Function Calling)
===========================================================================
Ein LLM kann von Natur aus nur Text erzeugen. Damit es trotzdem "aktiv"
werden kann (Dateien lesen, Shell-Befehle ausführen), geben wir ihm beim
API-Aufruf ein VERZEICHNIS verfügbarer Tools mit - als JSON-Schemas.

Das Modell führt dann KEINEN Code aus. Es antwortet stattdessen nur mit
einem strukturierten Wunsch: "bitte führe bash mit command='ls' aus".
WIR führen das Tool aus und geben das Ergebnis zurück. Erst dadurch
entsteht ein "Agent".

Ein Tool besteht also aus zwei Seiten:
  1. Das JSON-Schema  -> sieht das MODELL (damit es weiß, was es rufen kann
                         und welche Parameter es übergeben muss)
  2. Die run()-Methode -> führt der AGENT wirklich aus

Die ToolRegistry ist die zentrale Vermittlungsstelle: Sie hält alle
bekannten Tools, rendert die Schemas für den Provider und leitet
eingehende Calls an die richtige run()-Methode weiter.
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """Das Ergebnis einer Tool-Ausführung - was wir dem Modell zurückgeben.

    Wichtig: Fehler sind auch Ergebnisse! Wir werfen bei Tool-Fehlern
    NICHT, sondern kapseln den Fehlertext - denn das Modell soll die
    Chance bekommen, seinen Fehler zu erkennen und es anders zu versuchen.
    """

    output: str
    is_error: bool = False

    def to_content(self) -> str:
        """Der finale Text für die API-Nachricht.

        Fehler werden mit "ERROR:" markiert, damit das Modell ein
        unmissverständliches Signal bekommt, dass etwas schiefging.
        """
        return f"ERROR: {self.output}" if self.is_error else self.output


@dataclass
class Tool(abc.ABC):
    """Basis für alle Tools: Metadaten + eine run()-Methode.

    Ein konkretes Tool erbt davon und implementiert nur run().
    Beispiel: BashTool überschreibt run() und startet eine Subprocess.
    """

    name: str          # Name, unter dem das Modell das Tool ruft (z.B. "bash")
    description: str   # Beschreibung für das Modell - JE DETAILS, JE SÄUBERE
    parameters: dict   # JSON-Schema der Argumente (was das Modell übergeben darf)
    needs_approval: bool = False
    # ^ Sicherheitshebel: wenn True, fragt die CLI vor der Ausführung den Menschen
    #   ("y/n?"). Für gefährliche Tools wie bash und write_file gesetzt,
    #   für harmlose wie read_file nicht.


    @abc.abstractmethod
    def run(self, args: dict) -> ToolResult:
        """Hier passiert die eigentliche Arbeit.

        args enthält die (bereits geparsten!) Argumente vom Modell,
        z.B. {"command": "ls -la"}. Wirf ruhig Exceptions - die fängt
        die Registry ab und macht daraus ein sauberes ToolResult.
        """

    def schema(self) -> dict:
        """Rendert das OpenAI-Tool-Schema - das "Datenblatt" für das Modell.

        Das ist genau die Struktur, die beim API-Aufruf im Feld "tools"
        landet und dem Modell erklärt, was es aufrufen kann.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Verwalter aller verfügbaren Tools - die "Werkzeugkiste" des Agents.

    Verantwortlich für:
      - Registrieren (register)
      - Nachschlagen (get)
      - Schemas für die API (schemas)
      - Ausführen (execute) - inkl. Fehlerbehandlung
    """

    def __init__(self):
        # name -> Tool-Instanz. Damit rufen wir in O(1) das richtige Tool.
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Fügt ein Tool hinzu. Doppelte Namen sind ein Programmierfehler."""
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Sucht ein Tool nach Name; None, wenn unbekannt."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Alphabetische Liste aller Tool-Namen (z.B. für /status)."""
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        """Alle Schemas als Liste - genau das, was der Provider als
        `tools`-Parameter an die API schickt."""
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, raw_arguments: str | dict) -> ToolResult:
        """Führt einen Tool-Call aus. WIRFT NIE - Fehler werden Ergebnisse.

        Das ist DIE zentrale Stelle, an der Modell-Wunsch (der ja nur Text
        war) in echte Ausführung übersetzt wird.

        Ablauf:
          1. Tool nach Name suchen          (unbekannt? -> ToolResult-Fehler)
          2. JSON-Argumente parsen          (kaputt?     -> ToolResult-Fehler)
          3. tool.run(args) ausführen       (Crash?      -> ToolResult-Fehler)

        Der Modell bekommt bei jedem Fall einen klaren Fehlertext und
        kann selbst korrigieren - der Loop bricht nie wegen eines Tools.
        """
        tool = self._tools.get(name)
        if tool is None:
            # Das Modell hat ein Tool gerufen, das es gar nicht gibt.
            return ToolResult(f"unknown tool: {name}. Available: {self.names()}", is_error=True)

        # raw_arguments ist ein JSON-String (vom Modell). Wir parsen ihn
        # hier zentral, damit KEIN Tool sich selbst darum kümmern muss.
        if isinstance(raw_arguments, str):
            try:
                args = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError as e:
                return ToolResult(f"invalid JSON arguments: {e}", is_error=True)
        else:
            args = raw_arguments

        if not isinstance(args, dict):
            # z.B. wenn das Modell "true" oder "[1,2]" schickt
            return ToolResult("arguments must be a JSON object", is_error=True)

        try:
            return tool.run(args)
        except Exception as e:  # noqa: BLE001 - absichtlich: Fehler als Ergebnis
            return ToolResult(f"{type(e).__name__}: {e}", is_error=True)


def default_registry() -> ToolRegistry:
    """Die mitgelieferte Werkzeugkiste.

    Wird vom CLI und von Tests benutzt. Weitere Tools = einfach
    hier registrieren (oder eine neue Registry bauen).
    """
    from frigatebird.tools.bash_tool import BashTool
    from frigatebird.tools.edit_file import EditFileTool
    from frigatebird.tools.read_file import ReadFileTool
    from frigatebird.tools.write_file import WriteFileTool

    registry = ToolRegistry()
    registry.register(BashTool())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    return registry
