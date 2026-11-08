# frigatebird 🐦

A minimal AI harness / coding-agent CLI - built to learn how agents work.

## Quick start

```bash
pip install -e .
pytest                       # run the tests

# configure (one of):
export ANTHROPIC_API_KEY=sk-...
export OPENAI_API_KEY=sk-...
# or: export OLLAMA_HOST=http://localhost:11434/v1

frigatebird
```

Optional config file `~/.config/frigatebird/config.toml`:

```toml
[default]
provider = "anthropic"   # anthropic | openai-compat | ollama
model = "claude-sonnet-4-5"
```

## Architecture (read this top to bottom)

```
cli.py            REPL + slash commands + rendering (rich/prompt_toolkit)
  └─ agent.py     THE LOOP: model -> tool calls -> results -> model -> ...
       ├─ providers/  wire adapters: anthropic, openai-compat, ollama
       ├─ tools/      tool functions + JSON schemas (bash, read/write/edit)
       └─ messages.py internal message format (OpenAI-style lingua franca)
config.py         env + TOML config, provider factory
```

The three ideas worth studying:

1. **The agent loop** (`agent.py`): a chat model becomes an agent by looping -
   it may request tool calls, we execute them and feed results back, until it
   produces a final answer.
2. **Provider adapters** (`providers/`): one internal message format; each
   provider converts to/from its native wire protocol.
3. **Tool registry** (`tools/base.py`): schemas for the model, dispatch for
   us, and *errors as results* so a failing tool never kills the loop.

## Slash commands

`/help` `/status` `/model [name]` `/provider <name>` `/history` `/clear` `/quit`
