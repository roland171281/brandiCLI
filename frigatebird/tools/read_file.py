"""Datei lesen - das harmloseste Tool (deshalb ohne needs_approval).

Hier lernen wir zwei Dinge, die man in jedem Coding-Agent findet:

  1. Das Tool gibt KEIN Pathlib-Objekt zurück, sondern nur TEXT. Was das
     Modell sieht, ist immer ein String - mehr überträgt die API nicht.
  2. Binärdateien und Verzeichnisse werden explizit abgefangen, damit das
     Modell nicht müllige Binärdaten oder Cryptic-Errors zurückbekommt.
"""

from __future__ import annotations

from pathlib import Path

from frigatebird.tools.base import Tool, ToolResult

MAX_BYTES = 50_000  # Standard-Obergrenze: verhindert, dass RIESIGE Dateien
                    # das Kontextfenster des Modells sprengen


class ReadFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="read_file",
            description="Read a text file. Optionally read only a byte range.",
            parameters={
                "type": "object",
                "properties": {
                    # "path" ist Pflicht, "offset"/"limit" sind optional
                    "path": {"type": "string", "description": "Relative or absolute file path."},
                    "offset": {"type": "integer", "description": "Byte offset to start at (default 0)."},
                    "limit": {"type": "integer", "description": "Max bytes to read (default 50000)."},
                },
                "required": ["path"],
            },
        )

    def run(self, args: dict) -> ToolResult:
        path = Path(args["path"]).expanduser()  # "~" wird zu /home/roland
        offset = int(args.get("offset", 0))     # optional, Standard: vom Anfang
        limit = int(args.get("limit", MAX_BYTES))

        # Absichtlich eigene Fehlermeldungen statt Python-Exceptions:
        # Das Modell bekommt dann klaren, kontextreichen Text und kann
        # seine Pfadangabe sofort korrigieren.
        if not path.exists():
            return ToolResult(f"file not found: {path}", is_error=True)
        if path.is_dir():
            return ToolResult(f"is a directory: {path}", is_error=True)

        with path.open("rb") as f:
            # Binary-Modus, damit wir selbst kontrollieren, WIE wir lesen:
            # erst an die gewünschte Stelle springen, dann die Begrenzung lesen.
            f.seek(offset)
            data = f.read(limit)

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # z.B. beim Lesen einer .so- oder PNG-Datei: dem Modell klar sagen,
            # dass das nichts fürs Text-Modell ist
            return ToolResult("binary file - cannot display", is_error=True)

        return ToolResult(text)
