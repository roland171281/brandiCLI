"""Konfiguration: Umgebungsvariablen + optionale TOML-Datei.

===========================================================================
DAS PRIORITÄTEN-SCHEMA (wichtig! weiter oben gewinnt)
===========================================================================
  1. Umgebungsvariablen   (FRIGATEBIRD_PROVIDER, FRIGATEBIRD_MODEL, ...)
  2. ~/.config/frigatebird/config.toml
  3. eingebaute Defaults

Diese Reihenfolge ist Standard bei CLI-Tools: Env-Variablen überschreiben
Dateien, damit man in Shell-Skripten temporär abweichen kann, ohne die
Config anzufassen.

Die config.toml sieht so aus:
    [default]
    provider = "anthropic"        # anthropic | openai-compat | ollama | openrouter
    model = "claude-sonnet-4-5"
"""

from __future__ import annotations

import os
import tomllib  # seit Python 3.11 in der Standardbibliothek - kein 3rd-party nötig
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "frigatebird" / "config.toml"


@dataclass
class Config:
    """Die zusammengefasste Konfiguration - "was will der User?".

    Diese Klasse ist absichtlich DUMM: Sie sammelt nur Werte. Die
    Entscheidung, WELCHES Provider-Objekt daraus gebaut wird, trifft
    make_provider() weiter unten (eine Werkstatt / Factory).
    """

    provider: str = "anthropic"  # welcher API-Typ
    model: str = ""              # welches Modell; leer = Provider-Default
    api_key: str = ""            # API-Schlüssel (lieber via env oder config, nicht hartcodieren!)
    base_url: str = ""           # Override für OpenAI-kompatible APIs (z.B. Proxy)

    @classmethod
    def load(cls) -> "Config":
        """Liest die Config in fester Reihenfolge ein (siehe oben).

        Ablauf:
          1. Defaults anlegen (leeres Config-Objekt)
          2. TOML-Datei drüberlegen, falls vorhanden
          3. Env-Variablen drüberlegen (höchste Priorität)
        """
        cfg = cls()

        # --- 1) TOML-Datei -------------------------------------------------
        if CONFIG_PATH.exists():
            data = tomllib.loads(CONFIG_PATH.read_text()).get("default", {})
            for key in ("provider", "model", "api_key", "base_url"):
                if data.get(key):
                    setattr(cfg, key, data[key])

        # --- 2) Umgebungsvariablen (überschreiben die Datei!) --------------
        env_map = {
            "FRIGATEBIRD_PROVIDER": "provider",
            "FRIGATEBIRD_MODEL": "model",
            "FRIGATEBIRD_API_KEY": "api_key",
            "FRIGATEBIRD_BASE_URL": "base_url",
        }
        for env, attr in env_map.items():
            if os.environ.get(env):
                setattr(cfg, attr, os.environ[env])
        return cfg

    def status_line(self) -> str:
        """Eine kurze Statuszeile fürs /status-Kommando.

        Zeigt Provider, Modell und ob ein API-Key GEFUNDEN wurde.
        Achtung: Wir zeigen den Key selbst NIE an, nur "set"/"MISSING"!
        """
        bits = [f"provider={self.provider}", f"model={self.model or 'default'}"]
        if self.provider == "anthropic":
            bits.append("key=" + ("set" if os.environ.get("ANTHROPIC_API_KEY") or self.api_key else "MISSING"))
        elif self.provider == "openai-compat":
            bits.append("key=" + ("set" if os.environ.get("OPENAI_API_KEY") or self.api_key else "MISSING"))
        return "  ".join(bits)


def make_provider(cfg: Config):
    """Werkstatt: aus einer Config wird ein konkretes Provider-Objekt.

    Warum erst die Imports HIER und nicht oben? Lazy Imports: So laden wir
    die Provider-Module nur, wenn sie wirklich gebraucht werden. Das macht
    den Start schneller und verhindert Import-Ketten (config.py braucht
    providers/, providers/ braucht nichts von config.py).
    """
    from frigatebird.providers.anthropic import AnthropicProvider
    from frigatebird.providers.openai_compat import (
        OllamaProvider,
        OpenAICompatProvider,
        OpenRouterProvider,
    )

    if cfg.provider == "anthropic":
        return AnthropicProvider(api_key=cfg.api_key or None, model=cfg.model or None)
    if cfg.provider == "ollama":
        return OllamaProvider(model=cfg.model or None)
    if cfg.provider == "openrouter":
        return OpenRouterProvider(api_key=cfg.api_key or None, model=cfg.model or None)
    if cfg.provider == "openai-compat":
        return OpenAICompatProvider(
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or None,
            model=cfg.model or None,
        )
    raise ValueError(f"unknown provider: {cfg.provider!r} (anthropic | openai-compat | ollama | openrouter)")
