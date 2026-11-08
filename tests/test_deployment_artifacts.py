from __future__ import annotations


def test_alembic_root_prefers_runtime_working_directory(monkeypatch, tmp_path) -> None:
    from chatterlore.db.session import alembic_project_root

    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = alembic\n", encoding="utf-8"
    )
    (tmp_path / "alembic").mkdir()
    monkeypatch.chdir(tmp_path)

    assert alembic_project_root() == tmp_path
