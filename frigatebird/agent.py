"""Der Agent-Loop - DAS Herzstück jedes Coding-Agents.

===========================================================================
DAS GROSSE BILD: WIE WIRD EIN CHATMODELL ZU EINEM AGENTEN?
===========================================================================
Ein LLM kann von sich aus nur EINE Antwort pro Anfrage geben. Es kann
nicht selbst Dateien lesen, Shell-Befehle ausführen oder "nachdenken
und dann handeln". Der Trick, der daraus einen Agenten macht, ist ein
Kreislauf (LOOP):

    User-Frage
       |
       v
    LLM fragen --------------> antwortet NUR Text? -> fertig, Ende
       |                                   |
  will ein Tool rufen?                      v
       |                             finale Antwort
       v
    Tool ausführen (bash, read_file, ...)
       |
       v
    Ergebnis als "tool"-Nachricht an das LLM zurückgeben
       |
       +------> wieder von vorne (LLM fragen)

Erst dieser Kreislauf gibt dem Modell die Fähigkeit zu BEOBACHTEN
(Tool-Ergebnisse lesen) und zu HANDELN (Tools rufen), bevor es antwortet.

max_steps begrenzt die Anzahl der Runden, damit der Loop nicht endlos
läuft (z.B. wenn zwei Tools sich gegenseitig aufrufen).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from frigatebird.messages import ToolCall, assistant, tool_result
from frigatebird.providers.base import Provider
from frigatebird.tools.base import ToolRegistry, ToolResult


@dataclass
class AgentEvent:
    """Ein Ereignis während des Loops - die UI sieht nur davon etwas.

    Der Agent selbst hat KEINE Ahnung von rich oder Terminal - er gibt
    nur AgentEvents ab. Das CLI (cli.py) abonniert diese Events und
    rendert sie. Das trennt Logik und Darstellung sauber.

    Es gibt vier Event-Typen:
      "assistant_text"  -> das Modell hat FINALE Antwort geliefert
      "tool_start"      -> wir führen gerade ein Tool aus
      "tool_end"        -> das Tool ist fertig (mit Fehler-Flag)
      "turn_done"       -> reserviert für "eine User-Runde ist komplett"
    """

    kind: str          # s.o.
    text: str = ""     # bei "assistant_text": der Antwort-Text
    call: ToolCall | None = None  # bei tool_start/tool_end: WER aufgerufen wurde
    result: str = ""   # bei tool_end: das Ergebnis (oder der Fehlertext)
    is_error: bool = False  # bei tool_end: war es ein Fehler?


@dataclass
class Agent:
    """Die Agent-Instanz: Provider + Tools + Konversation.

    Warum ein dataclass? Damit die Felder (provider, registry, ...)
    einfach als Keyword-Argumente übergeben werden können und die
    Klasseninstanz automatisch __init__/__repr__ bekommt.
    """

    provider: Provider         # DIE LLM-Anbindung (Anthropic, OpenRouter, ...)
    registry: ToolRegistry     # DIE Werkzeugkiste (bash, read_file, ...)
    system_prompt: str = "You are frigatebird, a helpful coding assistant. Be concise."
    max_steps: int = 20        # max. LLM-Runden pro User-Nachricht (Endlos-Schutz)
    history: list[dict] = field(default_factory=list)
    # ^ die gesamte Konversation - DAS, was bei /history angezeigt wird

    # Hook: wird VOR Tools mit needs_approval=True aufgerufen.
    # Rückgabe False = "nein, nicht ausführen". Standard: immer ja (Auto-Approve).
    # Die CLI setzt das hier auf eine echte y/n-Abfrage (_approval_prompt).
    approve: Callable[[ToolCall], bool] = lambda call: True
    # Hook: wird für jedes AgentEvent aufgerufen (vom CLI zum Rendern genutzt).
    on_event: Callable[[AgentEvent], None] = lambda event: None

    def send_user(self, content: str) -> str:
        """Führt EINE komplette User-Runde aus. Gibt die finale Antwort zurück.

        Das ist die Schlüsselmethode: hier steckt der ganze Loop.
        Ablauf (siehe auch Diagramm oben):
          1. User-Nachricht an history anhängen
          2. Provider aufrufen -> Antwort in history
          3. Antwort ohne Tool-Calls? -> fertig zurückgeben
          4. Sonst: jeden Tool-Call ausführen, Ergebnis an history
          5. Springe zu Schritt 2 (solange max_steps nicht erreicht)
        """
        # Schritt 1: User-Nachricht in den Verlauf aufnehmen. WICHTIG:
        # Wir speichern ALLES (auch Tool-Ergebnisse), denn das Modell
        # bekommt bei jedem API-Call die GESAMTE Konversation neu.
        self.history.append({"role": "user", "content": content})

        for step in range(self.max_steps):
            # Schritt 2: Modell anfragen. Wir übergeben die komplette
            # history UND die Liste der verfügbaren Tools (als Schemas).
            reply = self.provider.chat(
                self.history, tools=self.registry.schemas()
            )
            # Die Antwort sofort in die History - denn auch sie gehört zur
            # Konversation (insbesondere die tool_calls-IDs!)
            self.history.append(reply)

            # Prüfen: WILL das Modell ein Tool aufrufen?
            calls = [ToolCall.from_dict(c) for c in reply.get("tool_calls") or []]

            if not calls:
                # Schritt 3a: Kein Tool-Call = FINALE Antwort. Fertig!
                self.history[-1]["content"] = reply["content"]
                # Der UI Bescheid geben, dass es etwas anzuzeigen gibt.
                # (Bugfix: wurde früher vergessen - die Antwort war da,
                # aber niemand hat sie gezeichnet!)
                self._emit("assistant_text", text=reply["content"])
                return reply["content"]

            # Schritt 4: Das Modell WILL etwas tun. Jede Anfrage ausführen...
            for call in calls:
                self._emit("tool_start", call)          # UI: "ich führe gerade X aus"
                tool = self.registry.get(call.name)
                if tool and tool.needs_approval and not self.approve(call):
                    # Der Mensch hat NEIN gesagt. Wir führen NICHTS aus,
                    # fügen aber TROTZDEM ein (Fehler-)Ergebnis hinzu.
                    # Warum? Die API verlangt: auf JEDEN tool_call MUSS ein
                    # tool-Ergebnis folgen, sonst ist die Konversation
                    # "kaputt" (das Modell wartet ewig auf seine Antwort).
                    result = ToolResult(f"user denied tool {call.name}", is_error=True)
                    output, is_error = result.to_content(), result.is_error
                else:
                    # Normale Ausführung. execute() WIRFT NIE - auch echte
                    # Fehler werden als Text-Ergebnis gekapselt. So kann das
                    # Modell seinen Fehler sehen und es anders versuchen.
                    result = self.registry.execute(call.name, call.arguments)
                    output, is_error = result.to_content(), result.is_error
                self._emit("tool_end", call, output, is_error)  # UI aktualisieren
                self.history.append(tool_result(call, output))  # Ergebnis in die History

            # ... und dann wieder zum Schleifenanfang (LLM fragen, mit den
            # frischen Tool-Ergebnissen in der History).

        # Wenn wir hier landen, hat das Modell in max_steps Runden keine
        # finale Antwort gefunden. Wir brechen ab, statt endlos weiterzulaufen.
        return "(reached max tool-call steps without a final answer)"

    def _emit(self, kind, call=None, output="", is_error=False, text=""):
        """Eine UI-Benachrichtigung über eine Loop-Stufe abgeben.

        text ist nur für kind="assistant_text" gefüllt; call/output/is_error
        nur für tool_start/tool_end.
        """
        self.on_event(AgentEvent(kind=kind, call=call, result=output, is_error=is_error, text=text))

    def clear(self):
        """Konversation zurücksetzen (z.B. für das /clear-Kommando)."""
        self.history.clear()
