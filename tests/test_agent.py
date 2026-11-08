"""Agent-loop test with a *fake* provider - no network, fully deterministic.

This is the pattern to study: the agent only depends on the Provider
interface, so we can script canned responses and verify the loop:
user msg -> assistant(tool_call) -> tool result -> assistant(final).
"""

from frigatebird.agent import Agent
from frigatebird.messages import assistant
from frigatebird.tools.base import Tool, ToolRegistry, ToolResult
from frigatebird.tools.bash_tool import BashTool


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, model=None):
        self.calls.append([dict(m) for m in messages])
        return self.script.pop(0)


class EchoTool(Tool):
    def __init__(self):
        super().__init__(
            name="echo",
            description="echo the input",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            needs_approval=True,  # the approval tests rely on this
        )

    def run(self, args):
        return ToolResult(f"echo: {args.get('text', '')}")


def test_full_loop_with_tool_call():
    provider = FakeProvider([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "echo", "arguments": '{"text": "hi"}'}},
        ]},
        {"role": "assistant", "content": "done", "tool_calls": []},
    ])
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(provider=provider, registry=registry)

    final = agent.send_user("run echo")

    assert final == "done"
    # the second model call must contain: user, assistant(tool_call), tool result
    second = provider.calls[1]
    assert [m["role"] for m in second] == ["user", "assistant", "tool"]
    assert second[2]["tool_call_id"] == "c1"
    assert second[2]["content"] == "echo: hi"


def test_denied_tool_still_gets_result():
    provider = FakeProvider([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "echo", "arguments": "{}"}},
        ]},
        {"role": "assistant", "content": "ok", "tool_calls": []},
    ])
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(provider=provider, registry=registry, approve=lambda call: False)

    agent.send_user("x")

    assert provider.calls[1][2]["content"] == "ERROR: user denied tool echo"


def test_bash_needs_approval():
    assert BashTool().needs_approval is True
