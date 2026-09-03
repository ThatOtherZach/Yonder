from pathlib import Path

import pytest

from scripts.run_test_shard import (
    SHARD_COUNT,
    assign_shards,
    discover_test_files,
    validate_assignment,
)


def _files(tmp_path: Path, sizes: list[int]) -> list[Path]:
    paths = []
    for index, size in enumerate(sizes):
        path = tmp_path / f"test_{index}.py"
        path.write_bytes(b"x" * size)
        paths.append(path)
    return paths


def test_assignment_is_deterministic_and_complete(tmp_path):
    files = _files(tmp_path, [100, 90, 80, 70, 60, 50, 40, 30])

    first = assign_shards(files)
    second = assign_shards(list(reversed(files)))

    assert first == second
    assert len(first) == SHARD_COUNT
    assert sorted(path for shard in first for path in shard) == sorted(files)
    validate_assignment(files, first)


def test_discovery_matches_pytest_filename_patterns_recursively(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    expected = [
        tmp_path / "feature_test.py",
        tmp_path / "test_top_level.py",
        nested / "test_nested.py",
    ]
    for path in [*expected, nested / "helper.py"]:
        path.write_text("")

    assert discover_test_files(tmp_path) == sorted(expected)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda shards, files: shards[0].remove(files[0]), "missing"),
        (lambda shards, files: shards[0].append(files[0]), "duplicated"),
        (
            lambda shards, files: shards[0].append(files[0].with_name("test_other.py")),
            "unexpected",
        ),
    ],
)
def test_assignment_validation_rejects_coverage_errors(tmp_path, mutate, message):
    files = _files(tmp_path, [100])
    shards = assign_shards(files)
    mutate(shards, files)

    with pytest.raises(RuntimeError, match=message):
        validate_assignment(files, shards)