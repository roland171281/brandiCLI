"""Das bash-Tool - das mächtigste (und gefährlichste) aller Tools.

Hier zeigt sich das zentrale Risiko eines Coding-Agents: Das Modell kann
BELIEBIGE Shell-Befehle anstoßen (auch `rm -rf /`!). Deshalb:

  1. needs_approval=True  -> die CLI fragt vorher den Menschen ("y/n?")
  2. Timeout              -> ein hängender Befehl (z.B. "tail -f") blockiert
                             den Agent nicht ewig
  3. Output-Truncation    -> ein Befehl mit Megabyte-Ausgabe würde sonst
                             den Context des Modells füllen (und Geld kosten)
"""

from __future__ import annotations

import subprocess

from frigatebird.tools.base import Tool, ToolResult


class BashTool(Tool):
    def __init__(self, timeout: float = 30.0):
        # Wir rufen den Tool-Basiskonstruktor mit den Metadaten auf:
        # Das ist ALLES, was das Modell sieht. run() kennt das Modell nicht.
        super().__init__(
            name="bash",
            description=(
                "Execute a shell command in the current working directory. "
                "Use for ls, grep, git, running tests, etc. "
                "Returns stdout+stderr and the exit code."
            ),
            parameters={
                # JSON-Schema: sagt dem Modell, WELCHE Argumente es übergeben
                # darf. "command" ist Pflicht, "timeout_seconds" optional.
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."},
                    "timeout_seconds": {"type": "integer", "description": "Optional timeout (default 30)."},
                },
                "required": ["command"],
            },
            needs_approval=True,  # IMMER erst den Menschen fragen!
        )
        self.timeout = timeout  # Standard-Timeout als Attribut speichern

    def run(self, args: dict) -> ToolResult:
        """Führt den Befehl wirklich aus (in einem Subprocess).

        WICHTIG: `shell=True` bedeutet, dass das eine echte Shell sieht -
        Pipes ("|"), Umleitung (">") und mehrere Befehle ("&&") funktionieren.
        Das ist gewollt, aber macht das Tool eben auch gefährlich.
        """
        command = args["command"]
        # Das Modell darf optional ein anderes Timeout mitgeben...
        timeout = float(args.get("timeout_seconds", self.timeout))
        try:
            proc = subprocess.run(
                command,
                shell=True,       # echte Shell -> Pipes, &&, > funktionieren
                capture_output=True,  # stdout und stderr AUF FANGEN statt anzuzeigen
                text=True,        # Bytes automatisch als Text dekodieren
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # Wenn der Befehl zu lange dauert: abbrechen und Fehler melden.
            # Das Modell kann dann z.B. "kill" versuchen oder einen Plan B wählen.
            return ToolResult(f"command timed out after {timeout}s", is_error=True)

        # Wir bauen die Rückgabe so auf, dass das Modell den kompletten
        # Kontext sieht: Exit-Code, stdout, stderr. Ohne Exit-Code wäre
        # für das Modell oft unklar, ob ein Befehl "geklappt" hat.
        parts = [f"exit_code={proc.returncode}"]
        if proc.stdout:
            parts.append(f"--- stdout ---\n{proc.stdout}")
        if proc.stderr:
            parts.append(f"--- stderr ---\n{proc.stderr}")
        out = "\n".join(parts)
        # Sehr lange Ausgabe abschneiden - Grund:
        #   1. kostet Geld (Tokens)
        #   2. kann das Context-Fenster des Modells sprengen
        #   3. zwingt das Modell, gezielter zu filtern (z.B. "grep | head")
        if len(out) > 10_000:
            out = out[:10_000] + "\n... (output truncated)"
        return ToolResult(out)
