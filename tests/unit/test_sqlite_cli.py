from __future__ import annotations

import json

from threvo_actions.cli import main


def test_sqlite_cli_requires_no_optional_dependency_and_reports_status(tmp_path, capsys) -> None:
    path = tmp_path / "actions.sqlite3"

    assert main(["sqlite", "inspect", "--database", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "applied_versions": [],
        "database": str(path),
        "pending_versions": [1],
    }

    assert main(["sqlite", "migrate", "--database", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "applied_versions": [1],
        "database": str(path),
        "pending_versions": [],
    }
