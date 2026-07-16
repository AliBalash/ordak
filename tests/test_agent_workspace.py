from __future__ import annotations

import pytest

from app.agent.workspace import resolve_workspace, resolve_workspace_path


def test_resolve_workspace_accepts_allowed_root(tmp_path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()

    resolved = resolve_workspace(str(workspace), (tmp_path.resolve(),))

    assert resolved.workspace == workspace.resolve()


def test_resolve_workspace_rejects_outside_root(tmp_path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    other_root = tmp_path / "other"
    other_root.mkdir()

    with pytest.raises(ValueError):
        resolve_workspace(str(workspace), (other_root.resolve(),))


def test_resolve_workspace_path_rejects_escape(tmp_path) -> None:
    with pytest.raises(ValueError):
        resolve_workspace_path(tmp_path.resolve(), "../outside.txt")
