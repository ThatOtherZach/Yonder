from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_test_shard import (
    SAFE_RUNTIME_SECONDS,
    SHARD_COUNT,
    assign_shards,
    discover_test_files,
    load_timing_manifest,
    record_timing_manifest,
    runtime_budget_errors,
    validate_assignment,
    pytest_runtest_logreport,
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


def test_assignment_prefers_fresh_measured_runtime_over_file_size(tmp_path):
    slow = _files(tmp_path, [10])[0]
    fast = _files(tmp_path, [100])[0]
    manifest = {
        "version": 1,
        "files": {
            slow.as_posix(): {
                "seconds": 9,
                "recorded_at": "2026-09-03T00:00:00Z",
                "source_size": 10,
            },
            fast.as_posix(): {
                "seconds": 1,
                "recorded_at": "2026-09-03T00:00:00Z",
                "source_size": 100,
            },
        },
        "shards": {},
    }

    shards = assign_shards(
        [fast, slow], shard_count=2, timing_manifest=manifest, now=1_788_393_600
    )

    assert shards == [[slow], [fast]]


def test_stale_or_size_mismatched_timing_uses_deterministic_fallback(tmp_path):
    first, second = _files(tmp_path, [100, 10])
    stale_manifest = {
        "version": 1,
        "files": {
            first.as_posix(): {
                "seconds": 0.01,
                "recorded_at": "2020-01-01T00:00:00Z",
                "source_size": 100,
            },
            second.as_posix(): {
                "seconds": 99,
                "recorded_at": "2026-09-03T00:00:00Z",
                "source_size": 999,
            },
        },
        "shards": {},
    }

    by_fallback = assign_shards(
        [first, second], shard_count=2, timing_manifest=stale_manifest, now=1_788_393_600
    )
    without_manifest = assign_shards([first, second], shard_count=2)

    assert by_fallback == without_manifest


def test_timing_manifest_is_merged_atomically_in_stable_json(tmp_path):
    manifest_path = tmp_path / "timings.json"
    record_timing_manifest(
        path=manifest_path,
        file_seconds={"tests/test_b.py": 2.3456},
        shard_seconds={2: 4.5678},
        source_sizes={"tests/test_b.py": 123},
        recorded_at="2026-09-03T00:00:00Z",
    )
    record_timing_manifest(
        path=manifest_path,
        file_seconds={"tests/test_a.py": 1.2345},
        shard_seconds={1: 3.4567},
        source_sizes={"tests/test_a.py": 45},
        recorded_at="2026-09-03T00:01:00Z",
    )

    assert load_timing_manifest(manifest_path, now=1_788_393_600)["files"] == {
        "tests/test_a.py": {
            "seconds": 1.234,
            "recorded_at": "2026-09-03T00:01:00Z",
            "source_size": 45,
        },
        "tests/test_b.py": {
            "seconds": 2.346,
            "recorded_at": "2026-09-03T00:00:00Z",
            "source_size": 123,
        },
    }
    assert '"tests/test_a.py"' in manifest_path.read_text(encoding="utf-8")
    assert manifest_path.read_text(encoding="utf-8").endswith("\n")


def test_runtime_budget_reports_projection_over_safety_margin(tmp_path):
    files = _files(tmp_path, [100, 100])
    manifest = {
        "version": 1,
        "files": {
            path.as_posix(): {
                "seconds": SAFE_RUNTIME_SECONDS + 1,
                "recorded_at": "2026-09-03T00:00:00Z",
                "source_size": path.stat().st_size,
            }
            for path in files
        },
        "shards": {},
    }
    shards = assign_shards(files, shard_count=2, timing_manifest=manifest, now=1_788_393_600)

    errors = runtime_budget_errors(shards, manifest, now=1_788_393_600)

    assert len(errors) == 2
    assert all("safe budget" in error for error in errors)


def test_timing_plugin_includes_fixture_setup_and_teardown(monkeypatch, tmp_path):
    import scripts.run_test_shard as runner

    monkeypatch.setattr(runner, "_PLUGIN_FILE_SECONDS", {})
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    test_file = tmp_path / "tests" / "test_fixture_cost.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_one(): pass\n", encoding="utf-8")

    for phase, duration in (("setup", 1.5), ("call", 2.0), ("teardown", 0.5)):
        pytest_runtest_logreport(
            SimpleNamespace(
                when=phase,
                nodeid="tests/test_fixture_cost.py::test_one",
                duration=duration,
            )
        )

    assert runner._PLUGIN_FILE_SECONDS == {
        "tests/test_fixture_cost.py": 4.0
    }