"""Das edit-File-Tool: exakten Text ersetzen.

Genau dieser Ansatz (alter Text -> neuer Text) steckt auch in Profi-Agenten
wie pi oder claude-code - hier siehst du das Prinzip in Miniatur.

===========================================================================
WARUM KEINE RegEx, WARUM KEIN "ZEILEN-NUMMERN"-TOOL?
===========================================================================
1. RegEx ist fehleranfällig und umständlich für das Modell.
2. Zeilennummern sind fragil: Nach jeder Änderung stimmen die Zahlen
   weiter unten nicht mehr.
3. "Alter Text -> Neuer Text" ist eindeutig, stabil und sprachunabhängig.

Das Risiko dieser Methode: Der alte Text MUSS im Original exakt so
vorkommen - einmal. Genau das prüfen wir hier:
  - 0 Treffer  -> Fehler (Modell hat sich vertippt oder zu wenig Kontext)
  - 2+ Treffer -> Fehler (un eindeutig; das Modell muss MEHR Kontext
                  mitschicken, damit klar ist, WELCHE Stelle gemeint ist)
"""

from __future__ import annotations

from pathlib import Path

from frigatebird.tools.base import Tool, ToolResult


class EditFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="edit_file",
            description=(
                "Replace an exact snippet of text in a file. old_text must match "
                "the file content exactly and be unique."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    # Welcher Text muss ersetzt werden (exakt wie in der Datei)
                    "old_text": {"type": "string", "description": "Exact text to replace."},
                    # Womit (das Ziel nach der Änderung)
                    "new_text": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
            needs_approval=True,  # Dateiinhalte verändern -> vorher fragen
        )

    def run(self, args: dict) -> ToolResult:
        path = Path(args["path"]).expanduser()
        if not path.exists():
            return ToolResult(f"file not found: {path}", is_error=True)

        # Die GANZE Datei als Text einlesen (wie viele einfache Editoren).
        text = path.read_text(encoding="utf-8")
        old, new = args["old_text"], args["new_text"]

        # --- Validierung (der Kern des Tools!) ---------------------------
        count = text.count(old)
        if count == 0:
            # Typische Ursache: das Modell hat Whitespace/Zeilenenden falsch
            # zitiert oder sich nur Teile der Datei angeschaut.
            return ToolResult("old_text not found in file (must match exactly)", is_error=True)
        if count > 1:
            # Mehrdeutig! Wir verweigern absichtlich die Ausführung, weil wir
            # nicht raten können, welche der mehreren Stellen gemeint war.
            return ToolResult(f"old_text matches {count} times - must be unique", is_error=True)

        # --- Die eigentliche Änderung (genau 1 Ersetzung) ---------------
        # Das dritte Argument (1) ist redundant, dokumentiert aber die
        # Absicht: höchstens EINE Ersetzung, nie "alle".
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return ToolResult(f"edited {path}")
