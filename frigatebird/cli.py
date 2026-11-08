"""Das REPL - interaktive Terminal-UI.

===========================================================================
DAS GROSSE BILD
===========================================================================
Dieses Modul verbindet alles zu einem interaktiven Werkzeug. Es besteht
aus zwei großen Teilen:

  A) DAS EINGABE-REPL (main): eine Endlosschleife, die
     1. eine Eingabezeile liest,
     2. prüft, ob sie mit "/" beginnt (dann: Kommando) oder ein
        an das Modell gerichteter Text ist,
     3. das Kommando ausführt bzw. agent.send_user() aufruft.

  B) DAS RENDERN (_print_event): reagiert auf AgentEvents aus dem
     Agent-Loop und gibt sie hübsch im Terminal aus.

Design-Prinzip: Die CLI ist die EINZIGE Stelle, die "Terminal" kennt.
Der Agent (agent.py) kennt kein rich, kein prompt_toolkit - er gibt nur
AgentEvents ab. So bleibt die Logik testbar und austauschbar.
"""

from __future__ import annotations

import json
import sys

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory   # Eingaben zwischen Starts merken
from rich.console import Console
from rich.markdown import Markdown               # Modell-Antworten als Markdown rendern
from rich.panel import Panel                     # Tool-Ausgaben in Boxen zeigen

from frigatebird import __version__
from frigatebird.agent import Agent, AgentEvent
from frigatebird.config import Config, make_provider
from frigatebird.tools.base import default_registry

console = Console()  # das zentrale rich-Ausgabeprogramm

# Was das Modell als System-Prompt sieht. Der System-Prompt bestimmt das
# "Persona" und die Regeln des Agents - sehr wichtig, sehr oft unterschätzt!
SYSTEM_PROMPT = """\
You are frigatebird, a minimal coding agent running in the user's terminal.
You have tools: bash, read_file, write_file, edit_file.
Rules of thumb:
- Look before you leap: read files before editing them.
- Prefer edit_file over write_file for existing files.
- Keep answers concise; show file paths you touched.
"""


def _print_event(event: AgentEvent) -> None:
    """Das UI-Rendern der AgentEvents.

    Wird im build_agent als Hook an den Agenten "angeklemmt" und von
    jedem Loop-Schritt aufgerufen (tool_start, tool_end, ...).
    """
    if event.kind == "assistant_text" and event.text:
        # Finale Antwort: als Markdown rendern (Code-Blöcke, Listen, ...)
        console.print()
        console.print(Markdown(event.text))
        console.print()
    elif event.kind == "tool_start":
        # Der Modell-WILL für den Tool-Call anzeigen:
        # Args als JSON, damit man exakt sieht, was das Modell "bestellt" hat.
        args = json.dumps(event.call.parsed_args(), ensure_ascii=False)
        console.print(f"[bold cyan]→ {event.call.name}[/bold cyan] {args}")
    elif event.kind == "tool_end":
        # Das Ergebnis als kleine Box. Rot, wenn es ein Fehler war.
        style = "red" if event.is_error else "dim"
        body = event.result[:400] + ("…" if len(event.result) > 400 else "")
        console.print(Panel(body, title=event.call.name, border_style=style, padding=(0, 1)))


def _approval_prompt(call) -> bool:
    """Human-in-the-loop: Fragen vor gefährlichen Tools.

    Wird vom Agenten über approve aufgerufen, bevor ein Tool mit
    needs_approval=True ausgeführt wird. Rückgabe True = ausführen.
    """
    console.print(f"[yellow]approve?[/yellow] {call.name} {call.arguments}")
    return console.input("[bold]y/n>[/bold] ").strip().lower() in ("y", "yes", "")


HELP = """\
[bold]commands[/bold]
  /help            show this help
  /status          provider, model, key status
  /model [name]    show or switch model
  /provider name   switch provider (anthropic | openai-compat | ollama | openrouter)
  /history         print the raw conversation (what the model actually sees!)
  /clear           wipe conversation history
  /quit            exit
Everything else is sent to the model as a user message.
"""


def main() -> None:
    """Der REPL-Einstiegspunkt (wird über pyproject als `frigatebird`-Befehl aufgerufen)."""
    console.print(f"[bold]frigatebird[/bold] v{__version__} - type /help for commands\n")
    cfg = Config.load()
    agent: Agent | None = None

    # Provider aufbauen. Wenn z.B. kein API-Key gesetzt ist, brechen wir
    # hier sauber ab, statt später mit halbfertigem Zustand zu laufen.
    try:
        agent = build_agent(cfg)
    except Exception as e:  # noqa: BLE001 - CLI darf nicht an Config-Fehlern sterben
        console.print(f"[red]provider setup failed:[/red] {e}")
        console.print("Set ANTHROPIC_API_KEY / OPENAI_API_KEY or ~/.config/frigatebird/config.toml")
        sys.exit(1)

    # prompt_toolkit verwaltet die Eingabezeile: Zeilenbearbeitung
    # (Pfeiltasten), Autovervollständigung, und (via FileHistory) die
    # Eingaben der letzten Sessions - wie bei bash/zsh.
    session = PromptSession(history=FileHistory(str(_history_path())))

    # ---------- DIE REPL-SCHLEIFE ------------------------------------------
    while True:
        try:
            line = session.prompt("› ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl-D / Ctrl-C in der Eingabezeile -> sauber beenden
            console.print("\nbye!")
            return
        if not line:
            continue  # leere Zeile ignorieren

        # Slash-Kommandos sind KEINE User-Nachricht an das Modell!
        if line.startswith("/"):
            if _handle_command(line, agent, cfg):
                continue      # alles OK, weiter zur nächsten Eingabe
            else:
                return  # /quit

        # Alles andere geht an den Agenten. Fehler (Netzwerk, API) fangen
        # wir hier ab, damit die REPL nicht bei jedem Hänger abschmiert.
        try:
            agent.send_user(line)
        except httpx.HTTPError as e:
            console.print(f"[red]network error:[/red] {e}")
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]error:[/red] {type(e).__name__}: {e}")


def build_agent(cfg: Config) -> Agent:
    """Agent bauen und die beiden Hooks anschließen.

    DAS ist die Verdrahtung, die die three Schichten zusammenbringt:
      - Provider aus der Config
      - die Standard-Werkzeugkiste
      - den System-Prompt
      - on_event  -> unser Renderer (_print_event)
      - approve   -> unsere y/n-Abfrage (_approval_prompt)
    """
    provider = make_provider(cfg)
    agent = Agent(provider=provider, registry=default_registry(), system_prompt=SYSTEM_PROMPT)
    agent.on_event = lambda event: _print_event(event)
    agent.approve = _approval_prompt
    return agent


def _history_path():
    """Datei, in der die Eingabe-Historie gespeichert wird."""
    d = _state_dir()
    return d / "input_history"


def _state_dir():
    """Das Verzeichnis für Laufzeitdaten (Eingabe-Historie).

    WICHTIG: bewusst ~/.local/state (XDG-Standard), NICHT ~/.config!
    Config = Einstellungen; State = Daten, die das Programm erzeugt.
    """
    from pathlib import Path
    d = Path.home() / ".local" / "state" / "frigatebird"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _handle_command(line: str, agent: Agent, cfg: Config) -> bool:
    """Ein Slash-Kommando ausführen. Gibt False nur für /quit zurück."""
    # "model gpt-4" wird zu cmd="model", rest=["gpt-4"]
    cmd, *rest = line[1:].split(maxsplit=1)

    if cmd == "help" or cmd == "?":
        console.print(HELP)
    elif cmd == "status":
        console.print(cfg.status_line())
        console.print(f"tools: {', '.join(agent.registry.names())}")
        console.print(f"history: {len(agent.history)} messages")
    elif cmd == "model":
        if rest:
            # Nur am Provider das Modell austauschen - Konversation bleibt
            agent.provider.model = rest[0]
            console.print(f"model -> {rest[0]}")
        else:
            console.print(f"model = {agent.provider.model}")
    elif cmd == "provider":
        if rest and rest[0] in ("anthropic", "openai-compat", "ollama", "openrouter"):
            cfg.provider = rest[0]
            # Einen FRISCHEN Agenten bauen (neuer Provider!)...
            new_agent = build_agent(cfg)
            # ... und die Felder in den bestehenden Agenten "hineinkopieren",
            # damit die History (und das agent-Objekt im main-Loop) gleich bleibt.
            agent.__dict__.update(new_agent.__dict__)
            console.print(f"provider -> {rest[0]}")
        else:
            console.print(f"provider = {agent.provider.name} (choices: anthropic, openai-compat, ollama, openrouter)")
    elif cmd == "history":
        # EXAKT das ausgeben, was das Modell sieht - super zum Lernen des
        # Protokolls! (tool_calls, tool_call_id, alles!)
        for msg in agent.history:
            extras = {
                k: msg[k] for k in ("tool_calls", "tool_call_id") if k in msg
            }
            console.print_json(json.dumps({"role": msg["role"], "content": msg["content"], **extras}))
    elif cmd == "clear":
        agent.clear()
        console.print("[dim]history cleared[/dim]")
    elif cmd in ("quit", "exit"):
        return False
    else:
        console.print(f"[red]unknown command:[/red] /{cmd} (try /help)")
    return True
