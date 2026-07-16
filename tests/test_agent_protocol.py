from __future__ import annotations

import base64

import pytest

from app.agent.protocol import (
    FinalResponse,
    ProtocolValidationError,
    parse_ordex_response,
)


def test_parse_run_response_with_single_json_object() -> None:
    parsed = parse_ordex_response(
        'RUN\n{"version":1,"id":"step-1","tool":"exec","cwd":".","argv":["pytest","-q"]}'
    )

    assert parsed.tool == "exec"
    assert parsed.id == "step-1"
    assert parsed.argv == ["pytest", "-q"]


def test_parse_run_response_with_fenced_json() -> None:
    parsed = parse_ordex_response(
        'RUN\n```json\n{"version":1,"id":"step-2","tool":"read_file","path":"README.md"}\n```'
    )

    assert parsed.tool == "read_file"
    assert parsed.path == "README.md"


def test_parse_run_response_with_rendered_json_fence_label() -> None:
    parsed = parse_ordex_response(
        'RUN\n\nJSON\n{"version":1,"id":"step-3","tool":"read_file","path":"app.js"}'
    )

    assert parsed.tool == "read_file"
    assert parsed.path == "app.js"


def test_parse_final_response() -> None:
    parsed = parse_ordex_response("FINAL\nImplemented the fix.")

    assert isinstance(parsed, FinalResponse)
    assert parsed.message == "Implemented the fix."


def test_parse_write_file_with_raw_multiline_content() -> None:
    parsed = parse_ordex_response(
        'RUN\n{"version":1,"id":"step-write","tool":"write_file","path":"index.html",'
        '"overwrite":false,"content":"<!doctype html>\n'
        '<html lang="en">\n<title>Calculator</title>\n</html>\n"}'
    )

    assert parsed.tool == "write_file"
    assert parsed.path == "index.html"
    assert parsed.content == (
        '<!doctype html>\n<html lang="en">\n<title>Calculator</title>\n</html>\n'
    )


def test_parse_write_file_decodes_json_escapes_once() -> None:
    parsed = parse_ordex_response(
        'RUN\n{"version":1,"id":"step-write","tool":"write_file","path":"app.js",'
        '"content":"const line = "first\\nsecond";\\nconsole.log(line);\\n"}'
    )

    assert parsed.content == 'const line = "first\nsecond";\nconsole.log(line);\n'


def test_parse_apply_patch_ignores_redundant_path() -> None:
    parsed = parse_ordex_response(
        'RUN\n{"version":1,"id":"step-patch","tool":"apply_patch","path":"app.js",'
        '"patch":"*** Begin Patch\\n*** Update File: app.js\\n@@\\n-old\\n+new\\n*** End Patch"}'
    )

    assert parsed.tool == "apply_patch"
    assert parsed.patch.startswith("*** Begin Patch")


def test_parse_write_file_decodes_base64_without_markdown_damage() -> None:
    content = (
        'from playwright.sync_api import sync_playwright\n'
        'url = "http://127.0.0.1:4173"\n'
        'value = `literal_*_content`\n'
    )
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    parsed = parse_ordex_response(
        'RUN\n{"version":1,"id":"step-base64","tool":"write_file","path":"verify.py",'
        f'"overwrite":true,"content_base64":"{encoded}"}}'
    )

    assert parsed.content == content
    assert parsed.content_base64 is None


def test_parse_apply_patch_decodes_base64() -> None:
    patch = "*** Begin Patch\n*** Add File: note.txt\n+hello\n*** End Patch"
    encoded = base64.b64encode(patch.encode("utf-8")).decode("ascii")

    parsed = parse_ordex_response(
        'RUN\n{"version":1,"id":"step-patch64","tool":"apply_patch",'
        f'"patch_base64":"{encoded}"}}'
    )

    assert parsed.patch == patch
    assert parsed.patch_base64 is None


@pytest.mark.parametrize(
    "payload",
    [
        'hello\nRUN\n{"version":1,"id":"step-1","tool":"exec","cwd":".","argv":["pwd"]}',
        'RUN\n{"version":1,"id":"step-1","tool":"exec","cwd":".","argv":["pwd"]}\n{"extra":true}',
        'RUN\n{"version":1,"id":"","tool":"exec","cwd":".","argv":["pwd"]}',
        'RUN\n{"version":1,"id":"step-write","tool":"write_file","path":"x.txt",'
        '"content":"hello\nworld"} trailing prose',
    ],
)
def test_invalid_protocol_responses_are_rejected(payload: str) -> None:
    with pytest.raises(ProtocolValidationError):
        parse_ordex_response(payload)
