import pytest

from alerts import db, rubrics


def _write(directory, version, prompt, model="m"):
    (directory / f"{version}.md").write_text(f"model: {model}\nnotes: note for {version}\n---\n{prompt}\n")


def test_repo_rubrics_parse_and_v1_is_first():
    files = rubrics.load_files()
    assert [r.id for r in files][:2] == ["v1", "v2"]
    assert all(r.model and r.system_prompt for r in files)
    assert files[0].system_prompt.startswith("You are a sell-side equity analyst")


def test_files_sort_numerically(tmp_path):
    for v in ("v10", "v2", "v1", "notes"):
        _write(tmp_path, v, "p")
    assert [r.id for r in rubrics.load_files(tmp_path)] == ["v1", "v2", "v10"]


def test_header_is_required(tmp_path):
    (tmp_path / "v1.md").write_text("just a prompt")
    with pytest.raises(ValueError, match="---"):
        rubrics.load_files(tmp_path)


def test_sync_first_is_active_rest_shadow_and_rows_are_immutable(conn, tmp_path, caplog):
    _write(tmp_path, "v1", "one")
    assert rubrics.sync(conn, tmp_path) == ["v1"]
    _write(tmp_path, "v2", "two")
    _write(tmp_path, "v1", "one, edited")
    assert rubrics.sync(conn, tmp_path) == ["v2"]
    assert "differs from what was recorded" in caplog.text
    assert [(r.id, r.status, r.system_prompt, r.notes) for r in rubrics.stored(conn)] == [
        ("v1", "active", "one", "note for v1"), ("v2", "shadow", "two", "note for v2")]
    assert [r.id for r in rubrics.scoring(conn)] == ["v1", "v2"]


def test_promote_and_retire(conn, tmp_path):
    for v in ("v1", "v2", "v3"):
        _write(tmp_path, v, v)
    rubrics.sync(conn, tmp_path)
    rubrics.promote(conn, "v2")
    assert {r.id: r.status for r in rubrics.stored(conn)} == {"v1": "retired", "v2": "active", "v3": "shadow"}
    assert [r.id for r in rubrics.scoring(conn)] == ["v2", "v3"]
    with pytest.raises(ValueError):
        rubrics.retire(conn, "v2")  # can't leave no active rubric
    with pytest.raises(ValueError):
        rubrics.promote(conn, "v1")  # retired versions don't come back
    rubrics.retire(conn, "v3")
    assert [r.id for r in rubrics.scoring(conn)] == ["v2"]
