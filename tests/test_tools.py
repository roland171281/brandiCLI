from frigatebird.tools.base import ToolRegistry
from frigatebird.tools.bash_tool import BashTool
from frigatebird.tools.read_file import ReadFileTool
from frigatebird.tools.write_file import WriteFileTool
from frigatebird.tools.edit_file import EditFileTool


def make_registry() -> ToolRegistry:
    r = ToolRegistry()
    for t in (BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool()):
        r.register(t)
    return r


def test_registry_dispatch_and_errors(tmp_path):
    r = make_registry()
    f = tmp_path / "hello.txt"

    out = r.execute("write_file", {"path": str(f), "content": "one\ntwo\n"})
    assert not out.is_error
    assert f.read_text() == "one\ntwo\n"

    out = r.execute("read_file", {"path": str(f)})
    assert out.output == "one\ntwo\n"

    out = r.execute("read_file", {"path": str(tmp_path / "nope.txt")})
    assert out.is_error

    out = r.execute("nonexistent_tool", "{}")
    assert out.is_error

    out = r.execute("write_file", "{not json")
    assert out.is_error


def test_edit_requires_unique_match(tmp_path):
    r = make_registry()
    f = tmp_path / "code.py"
    f.write_text("a = 1\na = 1\n")

    out = r.execute("edit_file", {"path": str(f), "old_text": "a = 1", "new_text": "a = 2"})
    assert out.is_error and "2 times" in out.output

    f.write_text("a = 1\nb = 2\n")
    out = r.execute("edit_file", {"path": str(f), "old_text": "a = 1", "new_text": "a = 2"})
    assert not out.is_error
    assert f.read_text() == "a = 2\nb = 2\n"


def test_bash_tool(tmp_path):
    t = BashTool()
    out = t.run({"command": "echo hi"})
    assert not out.is_error and "hi" in out.output
    out = t.run({"command": "exit 3"})
    assert "exit_code=3" in out.output


def test_schemas_shape():
    r = make_registry()
    schemas = r.schemas()
    assert len(schemas) == 4
    for s in schemas:
        assert s["type"] == "function"
        assert s["function"]["name"] and s["function"]["parameters"]["type"] == "object"
