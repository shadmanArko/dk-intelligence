"""ADR 0011: a sealed model file changes only together with a deliberate model change (and its lock entry)."""

from intel.sealed import diff


def test_sealed_code_matches_the_lock():
    problems = diff()
    assert not problems, (
        "Sealed model code changed: " + "; ".join(problems) + ".\n"
        "Packages built before this change will be refused by the VPS as 'incompatible'. If this is a deliberate "
        "model change, run `uv run python -m intel.sealed --update`, commit the lock with it, and retrain. "
        "Cosmetic edits (types, formatting) to sealed files are not allowed: see docs/adr/0011."
    )


def test_a_changed_file_is_reported(tmp_path):
    import json

    from intel.sealed import current

    lock = current()
    name = next(iter(lock))
    first = next(iter(lock[name]))
    lock[name][first] = "0" * 64
    lock["gone_model"] = {}
    p = tmp_path / "lock.json"
    p.write_text(json.dumps(lock))
    assert diff(p) == sorted(
        [f"{name}: {first} changed", "gone_model: in the lock but no plug-in produces it any more"]
    )


def test_cli_check_and_update(tmp_path, monkeypatch, capsys):
    import intel.sealed as sealed

    lock = tmp_path / "lock.json"
    monkeypatch.setattr(sealed, "LOCK", lock)
    assert sealed.main([]) == 1  # no lock yet: every model is new
    assert "new model" in capsys.readouterr().out
    assert sealed.main(["--update"]) == 0 and lock.exists()
    assert sealed.main([]) == 0
    assert "matches the lock" in capsys.readouterr().out
