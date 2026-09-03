from __future__ import annotations

from pathlib import Path

import pytest

from scripts import test_shards


def _write_test(path: Path, count: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"def test_{index}():\n    assert True\n" for index in range(count)),
        encoding="utf-8",
    )
    return path


def test_assignment_is_complete_unique_and_deterministic(tmp_path):
    files = [_write_test(tmp_path / f"test_{name}.py", count) for name, count in (
        ("large", 12),
        ("medium", 7),
        ("small", 2),
        ("tiny", 1),
    )]

    first = test_shards.assign_shards(files, shard_count=3)
    second = test_shards.assign_shards(list(reversed(files)), shard_count=3)

    assert [[item.path for item in shard] for shard in first] == [
        [item.path for item in shard] for shard in second
    ]
    test_shards.check_assignment(first, files)
    assert sorted(item.path for shard in first for item in shard) == sorted(
        path.resolve() for path in files
    )


def test_assignment_rejects_invalid_shard_count(tmp_path):
    path = _write_test(tmp_path / "test_one.py", 1)
    with pytest.raises(ValueError, match="at least 1"):
        test_shards.assign_shards([path], shard_count=0)


def test_check_assignment_detects_duplicate_and_missing_files(tmp_path):
    first = _write_test(tmp_path / "test_first.py", 1)
    second = _write_test(tmp_path / "test_second.py", 1)
    item = test_shards.weigh(first)

    with pytest.raises(RuntimeError, match="duplicate"):
        test_shards.check_assignment([[item], [item]], [first, second])

    with pytest.raises(RuntimeError, match="incomplete"):
        test_shards.check_assignment([[item]], [first, second])


def test_pytest_command_contains_timing_and_every_file(tmp_path, monkeypatch):
    monkeypatch.setattr(test_shards, "ROOT", tmp_path)
    first = _write_test(tmp_path / "tests" / "test_first.py", 1)
    second = _write_test(tmp_path / "tests" / "test_second.py", 1)
    shard = [test_shards.weigh(first), test_shards.weigh(second)]

    command = test_shards.pytest_command(shard, ["-x"])

    assert command[:5] == [
        test_shards.sys.executable,
        "-m",
        "pytest",
        "-q",
        "--durations=20",
    ]
    assert command[-3:] == [
        "tests/test_first.py",
        "tests/test_second.py",
        "-x",
    ]