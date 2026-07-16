from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator


_RUN_FENCE_RE = re.compile(
    r"^```(?:json)?\s*\n(?P<body>\{.*\})\n```$",
    re.DOTALL,
)
_WRITE_FILE_CONTENT_RE = re.compile(
    r'^(?P<prefix>\{.*?"content"\s*:\s*)"(?P<content>.*)"\s*\}$',
    re.DOTALL,
)


class ProtocolValidationError(ValueError):
    pass


def _decode_json_string_escapes(value: str) -> str:
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            output.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped in escapes:
            output.append(escapes[escaped])
            index += 2
            continue
        if escaped == "u" and index + 5 < len(value):
            codepoint = value[index + 2 : index + 6]
            try:
                output.append(chr(int(codepoint, 16)))
                index += 6
                continue
            except ValueError:
                pass
        output.extend(("\\", escaped))
        index += 2
    return "".join(output)


class _BaseAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    id: str = Field(min_length=1, max_length=200)
    tool: str


class ExecAction(_BaseAction):
    tool: Literal["exec"]
    cwd: str = Field(min_length=1, max_length=4096)
    argv: list[str] = Field(min_length=1, max_length=256)
    timeout_seconds: int | None = Field(default=None, ge=1, le=1800)


class ReadFileAction(_BaseAction):
    tool: Literal["read_file"]
    path: str = Field(min_length=1, max_length=4096)
    offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, ge=1, le=1_000_000)


class ListDirectoryAction(_BaseAction):
    tool: Literal["list_directory"]
    path: str = Field(min_length=1, max_length=4096)
    max_depth: int = Field(default=2, ge=0, le=12)
    max_entries: int = Field(default=500, ge=1, le=5000)


class WriteFileAction(_BaseAction):
    tool: Literal["write_file"]
    path: str = Field(min_length=1, max_length=4096)
    content: str | None = Field(default=None, max_length=2_000_000)
    content_base64: str | None = Field(default=None, max_length=2_700_000)
    create_parents: bool = False
    overwrite: bool = False

    @model_validator(mode="after")
    def decode_content(self) -> WriteFileAction:
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("write_file requires exactly one of content or content_base64.")
        if self.content_base64 is not None:
            try:
                decoded = base64.b64decode(self.content_base64, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError("write_file content_base64 must be valid base64-encoded UTF-8.") from exc
            if len(decoded) > 2_000_000:
                raise ValueError("write_file decoded content exceeds the maximum size.")
            self.content = decoded
            self.content_base64 = None
        return self


class ApplyPatchAction(_BaseAction):
    tool: Literal["apply_patch"]
    patch: str | None = Field(default=None, min_length=1, max_length=2_000_000)
    patch_base64: str | None = Field(default=None, max_length=2_700_000)

    @model_validator(mode="after")
    def decode_patch(self) -> ApplyPatchAction:
        if (self.patch is None) == (self.patch_base64 is None):
            raise ValueError("apply_patch requires exactly one of patch or patch_base64.")
        if self.patch_base64 is not None:
            try:
                decoded = base64.b64decode(self.patch_base64, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError("apply_patch patch_base64 must be valid base64-encoded UTF-8.") from exc
            if not decoded:
                raise ValueError("apply_patch decoded patch must not be empty.")
            if len(decoded) > 2_000_000:
                raise ValueError("apply_patch decoded patch exceeds the maximum size.")
            self.patch = decoded
            self.patch_base64 = None
        return self


AgentAction = Annotated[
    ExecAction | ReadFileAction | ListDirectoryAction | WriteFileAction | ApplyPatchAction,
    Field(discriminator="tool"),
]
ACTION_ADAPTER = TypeAdapter(AgentAction)


@dataclass(slots=True)
class FinalResponse:
    message: str


def _first_non_empty_line(payload: str) -> tuple[str | None, str]:
    lines = payload.splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            remaining = "\n".join(lines[index + 1 :])
            return line.strip(), remaining
    return None, ""


def _parse_single_json_object(raw_body: str) -> dict[str, object]:
    body = raw_body.strip()
    if not body:
        raise ProtocolValidationError("RUN responses must include exactly one JSON object.")
    fenced_match = _RUN_FENCE_RE.fullmatch(body)
    if fenced_match:
        body = fenced_match.group("body").strip()
    else:
        rendered_lines = body.splitlines()
        if (
            rendered_lines
            and rendered_lines[0].strip().lower() == "json"
            and "\n".join(rendered_lines[1:]).lstrip().startswith("{")
        ):
            # ChatGPT's rendered code block innerText keeps the language label
            # while removing the backtick fence.
            body = "\n".join(rendered_lines[1:]).strip()
    decoder = json.JSONDecoder()
    try:
        parsed, end_index = decoder.raw_decode(body)
    except json.JSONDecodeError as exc:
        write_match = _WRITE_FILE_CONTENT_RE.fullmatch(body)
        if not write_match:
            raise ProtocolValidationError("RUN response JSON is invalid.") from exc
        repaired = (
            write_match.group("prefix")
            + json.dumps(
                _decode_json_string_escapes(write_match.group("content")),
                ensure_ascii=False,
            )
            + "}"
        )
        try:
            parsed, end_index = decoder.raw_decode(repaired)
        except json.JSONDecodeError as repaired_exc:
            raise ProtocolValidationError("RUN response JSON is invalid.") from repaired_exc
        body = repaired
    if not isinstance(parsed, dict):
        raise ProtocolValidationError("RUN response must contain one JSON object.")
    if body[end_index:].strip():
        raise ProtocolValidationError("RUN response must not contain trailing prose or multiple JSON objects.")
    return parsed


def parse_ordex_response(payload: str) -> FinalResponse | AgentAction:
    first_line, remainder = _first_non_empty_line(payload)
    if first_line is None:
        raise ProtocolValidationError(
            "Empty model response. Respond with exactly RUN plus one JSON action or FINAL plus the final answer."
        )
    if first_line == "FINAL":
        return FinalResponse(message=remainder)
    if first_line != "RUN":
        raise ProtocolValidationError(
            "The first non-empty line must be exactly RUN or FINAL."
        )
    action_payload = _parse_single_json_object(remainder)
    # Models commonly add a redundant path alongside an apply_patch payload.
    # The patch headers remain the sole source of truth for affected files.
    if action_payload.get("tool") == "apply_patch":
        action_payload.pop("path", None)
    try:
        parsed = ACTION_ADAPTER.validate_python(action_payload)
    except ValidationError as exc:
        raise ProtocolValidationError(str(exc)) from exc
    if not parsed.id.strip():
        raise ProtocolValidationError("RUN action IDs must not be empty.")
    return parsed
