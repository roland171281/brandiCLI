"""Datei schreiben (neu anlegen oder komplett überschreiben).

Das gefährlichste Datei-Tool, weil es ganze Dateien ersetzen kann - daher
needs_approval=True. Merkregel:

  - Neue Datei anlegen / komplett neu schreiben   -> write_file
  - Nur eine Stelle in bestehender Datei ändern  -> edit_file (präziser,
    günstiger, weniger risikoreich)
"""

from __future__ import annotations

from pathlib import Path

from frigatebird.tools.base import Tool, ToolResult


class WriteFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="write_file",
            description="Create or overwrite a file with the given content. Parent dirs are created.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    # Wichtig: das Modell übergibt hier den KOMPLETTEN Dateiinhalt
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
            needs_approval=True,  # ganze Dateien überschreiben -> vorher fragen!
        )

    def run(self, args: dict) -> ToolResult:
        path = Path(args["path"]).expanduser()
        # Elternordner automatisch anlegen, damit z.B. write("src/x/y.py")
        # nicht an "kein Ordner" scheitert. exist_ok=True: keine Fehlermeldung,
        # wenn der Ordner schon existiert.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return ToolResult(f"wrote {len(args['content'])} bytes to {path}")
