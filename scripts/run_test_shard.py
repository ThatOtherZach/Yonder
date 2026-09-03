#!/usr/bin/env python3
"""Discover, validate, balance, and run deterministic pytest file shards.

The checked-in timing manifest is deliberately advisory: it improves balancing
when it has a recent, size-matching measurement, but it can never affect test
coverage.  New or stale files fall back to a deterministic source-size proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from fcntl import LOCK_EX, flock
from pathlib import Path
from typing import Sequence

SHARD_COUNT = 6
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = PROJECT_ROOT / "tests"
TIMINGS_PATH = PROJECT_ROOT / ".test_shard_timings.json"
SHARD_TIMEOUT_SECONDS = 270
SAFETY_MARGIN_SECONDS = 30
SAFE_RUNTIME_SECONDS = SHARD_TIMEOUT_SECONDS - SAFETY_MARGIN_SECONDS
TIMING_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
TIMING_MANIFEST_VERSION = 1
_TIMING_OUTPUT_ENV = "YONDER_SHARD_TIMING_OUTPUT"
# A source-size proxy in seconds.  This is intentionally conservative enough
# to make a first run useful for the safety check while measured records learn
# the actual cost of each file.
_TIMING_SCALE = 1_000.0


def _manifest_key(path: Path) -> str:
    """Return the stable project-relative key used by the timing manifest."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _now_iso(now: float | None = None) -> str:
    timestamp = datetime.fromtimestamp(
        time.time() if now is None else now, tz=timezone.utc
    )
    return timestamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_recorded_at(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds < 24 * 60 * 60 else None


def load_timing_manifest(
    path: Path = TIMINGS_PATH, *, now: float | None = None
) -> dict:
    """Load a valid timing manifest, returning an empty manifest on bad input.

    Invalid records are ignored individually.  This makes a hand-edited or
    partially-written manifest harmless: discovery and assignment still use
    every current test file and the deterministic fallback.
    """
    empty = {"version": TIMING_MANIFEST_VERSION, "files": {}, "shards": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, dict) or raw.get("version") != TIMING_MANIFEST_VERSION:
        return empty

    current_time = time.time() if now is None else now
    files: dict[str, dict] = {}
    raw_files = raw.get("files")
    if isinstance(raw_files, dict):
        for key, record in raw_files.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            seconds = _valid_seconds(record.get("seconds"))
            recorded_at = _parse_recorded_at(record.get("recorded_at"))
            source_size = record.get("source_size")
            if (
                seconds is None
                or recorded_at is None
                or not isinstance(source_size, int)
                or source_size < 0
                or current_time - recorded_at > TIMING_MAX_AGE_SECONDS
                or recorded_at - current_time > 60
            ):
                continue
            files[key] = {
                "seconds": seconds,
                "recorded_at": record["recorded_at"],
                "source_size": source_size,
            }

    shards: dict[str, dict] = {}
    raw_shards = raw.get("shards")
    if isinstance(raw_shards, dict):
        for key, record in raw_shards.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            seconds = _valid_seconds(record.get("seconds"))
            recorded_at = _parse_recorded_at(record.get("recorded_at"))
            if (
                seconds is None
                or recorded_at is None
                or current_time - recorded_at > TIMING_MAX_AGE_SECONDS
                or recorded_at - current_time > 60
            ):
                continue
            shards[key] = {
                "seconds": seconds,
                "recorded_at": record["recorded_at"],
            }
    return {"version": TIMING_MANIFEST_VERSION, "files": files, "shards": shards}


def _fallback_seconds(path: Path) -> float:
    """Estimate a new file's cost from stable source metadata."""
    return max(0.01, path.stat().st_size / _TIMING_SCALE)


def _timing_for(
    path: Path, timing_manifest: dict | None, *, now: float | None = None
) -> tuple[float, bool]:
    """Return (estimated seconds, measured) for one current file."""
    if timing_manifest:
        record = timing_manifest.get("files", {}).get(_manifest_key(path))
        if isinstance(record, dict):
            seconds = _valid_seconds(record.get("seconds"))
            recorded_at = _parse_recorded_at(record.get("recorded_at"))
            source_size = record.get("source_size")
            current_time = time.time() if now is None else now
            if (
                seconds is not None
                and recorded_at is not None
                and isinstance(source_size, int)
                and source_size == path.stat().st_size
                and current_time - recorded_at <= TIMING_MAX_AGE_SECONDS
                and recorded_at - current_time <= 60
            ):
                return max(0.01, seconds), True
    return _fallback_seconds(path), False


def _write_json_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


@contextmanager
def _manifest_lock(path: Path):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        flock(lock_file.fileno(), LOCK_EX)
        yield


def record_timing_manifest(
    *,
    path: Path = TIMINGS_PATH,
    file_seconds: dict[str, float],
    shard_seconds: dict[int, float] | None = None,
    source_sizes: dict[str, int] | None = None,
    recorded_at: str | None = None,
) -> None:
    """Merge completed timing data into the stable, reviewable manifest."""
    stamp = recorded_at or _now_iso()
    with _manifest_lock(path):
        manifest = load_timing_manifest(path)
        files = manifest.setdefault("files", {})
        for key, seconds in sorted(file_seconds.items()):
            valid_seconds = _valid_seconds(seconds)
            if valid_seconds is None:
                continue
            size = (source_sizes or {}).get(key)
            if not isinstance(size, int) or size < 0:
                continue
            files[key] = {
                "seconds": round(valid_seconds, 3),
                "recorded_at": stamp,
                "source_size": size,
            }
        if shard_seconds:
            shards = manifest.setdefault("shards", {})
            for shard, seconds in sorted(shard_seconds.items()):
                valid_seconds = _valid_seconds(seconds)
                if valid_seconds is not None:
                    shards[str(shard)] = {
                        "seconds": round(valid_seconds, 3),
                        "recorded_at": stamp,
                    }
        _write_json_atomically(path, manifest)


def _read_plugin_timings(path: Path) -> dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in raw.items():
        seconds = _valid_seconds(value)
        if isinstance(key, str) and seconds is not None:
            result[key] = seconds
    return result


# These hooks are loaded into the child pytest process with ``-p``.  Keeping
# them here avoids a second plugin package and makes the timing behavior travel
# with the runner that consumes it.
_PLUGIN_FILE_SECONDS: dict[str, float] = {}


def pytest_runtest_logreport(report) -> None:
    if report.when not in {"setup", "call", "teardown"}:
        return
    node_file = report.nodeid.split("::", 1)[0]
    path = (PROJECT_ROOT / node_file).resolve()
    key = _manifest_key(path)
    _PLUGIN_FILE_SECONDS[key] = _PLUGIN_FILE_SECONDS.get(key, 0.0) + report.duration


def pytest_sessionfinish(session, exitstatus) -> None:
    output = os.environ.get(_TIMING_OUTPUT_ENV)
    if output:
        _write_json_atomically(Path(output), _PLUGIN_FILE_SECONDS)


def discover_test_files(tests_dir: Path = TESTS_DIR) -> list[Path]:
    """Return every pytest-style test module recursively in stable path order."""
    files = {
        path
        for pattern in ("test_*.py", "*_test.py")
        for path in tests_dir.rglob(pattern)
        if path.is_file()
    }
    return sorted(files, key=lambda path: path.as_posix())


def assign_shards(
    files: Sequence[Path],
    shard_count: int = SHARD_COUNT,
    timing_manifest: dict | None = None,
    *,
    now: float | None = None,
) -> list[list[Path]]:
    """Balance files by measured runtime, with a stable size fallback.

    The sort and tie-breakers intentionally include the path so a missing,
    stale, or malformed timing record cannot change coverage or introduce
    run-to-run randomness.
    """
    if shard_count < 1:
        raise ValueError("shard_count must be positive")

    shards: list[list[Path]] = [[] for _ in range(shard_count)]
    weights = [0.0] * shard_count
    estimates = {
        path: _timing_for(path, timing_manifest, now=now) for path in files
    }
    ranked = sorted(
        files,
        key=lambda path: (-estimates[path][0], path.as_posix()),
    )
    for path in ranked:
        shard_index = min(range(shard_count), key=lambda index: (weights[index], index))
        shards[shard_index].append(path)
        weights[shard_index] += estimates[path][0]

    for shard in shards:
        shard.sort(key=lambda path: path.as_posix())
    return shards


def validate_assignment(files: Sequence[Path], shards: Sequence[Sequence[Path]]) -> None:
    """Fail if discovered files and assigned files are not exactly one-to-one."""
    discovered = list(files)
    assigned = [path for shard in shards for path in shard]
    missing = sorted(set(discovered) - set(assigned))
    unexpected = sorted(set(assigned) - set(discovered))
    duplicates = sorted({path for path in assigned if assigned.count(path) > 1})

    problems: list[str] = []
    if missing:
        problems.append("missing: " + ", ".join(path.name for path in missing))
    if unexpected:
        problems.append("unexpected: " + ", ".join(path.name for path in unexpected))
    if duplicates:
        problems.append("duplicated: " + ", ".join(path.name for path in duplicates))
    if len(shards) != SHARD_COUNT:
        problems.append(f"expected {SHARD_COUNT} shards, got {len(shards)}")
    if problems:
        raise RuntimeError("Invalid test shard assignment: " + "; ".join(problems))


def projected_shard_seconds(
    shards: Sequence[Sequence[Path]],
    timing_manifest: dict | None = None,
    *,
    now: float | None = None,
) -> list[float]:
    """Return the runtime projection for each shard in seconds."""
    return [
        sum(_timing_for(path, timing_manifest, now=now)[0] for path in shard)
        for shard in shards
    ]


def runtime_budget_errors(
    shards: Sequence[Sequence[Path]],
    timing_manifest: dict | None = None,
    *,
    safe_runtime_seconds: float = SAFE_RUNTIME_SECONDS,
    now: float | None = None,
) -> list[str]:
    """Describe projected shards that do not retain the configured headroom."""
    projections = projected_shard_seconds(shards, timing_manifest, now=now)
    return [
        (
            f"shard {index}: projected {seconds:.1f}s exceeds "
            f"safe budget {safe_runtime_seconds:.1f}s "
            f"({SHARD_TIMEOUT_SECONDS}s timeout minus "
            f"{SAFETY_MARGIN_SECONDS}s margin)"
        )
        for index, seconds in enumerate(projections, start=1)
        if seconds > safe_runtime_seconds
    ]


def print_assignment(
    shards: Sequence[Sequence[Path]], timing_manifest: dict | None = None
) -> None:
    total_files = sum(len(shard) for shard in shards)
    print(f"Validated {total_files} test files across {len(shards)} shards.")
    for index, shard in enumerate(shards, start=1):
        weight = sum(path.stat().st_size for path in shard)
        projected = sum(
            _timing_for(path, timing_manifest)[0] for path in shard
        )
        measured = sum(
            1 for path in shard if _timing_for(path, timing_manifest)[1]
        )
        print(
            f"\nShard {index}: {len(shard)} files, {weight:,} bytes, "
            f"projected {projected:.1f}s ({measured} measured)"
        )
        for path in shard:
            print(f"  {_manifest_key(path)}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="verify complete coverage")
    action.add_argument("--list", action="store_true", help="show all shard memberships")
    action.add_argument("--shard", type=int, metavar=f"1-{SHARD_COUNT}", help="run one shard")
    parser.add_argument(
        "--timings",
        type=Path,
        default=TIMINGS_PATH,
        help=f"timing manifest (default: {TIMINGS_PATH.name})",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="extra pytest arguments (place after --)",
    )
    args = parser.parse_args(argv)
    if args.shard is not None and not 1 <= args.shard <= SHARD_COUNT:
        parser.error(f"--shard must be between 1 and {SHARD_COUNT}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    files = discover_test_files()
    if not files:
        print(f"No test files found in {TESTS_DIR}", file=sys.stderr)
        return 2

    timing_manifest = load_timing_manifest(args.timings)
    shards = assign_shards(files, timing_manifest=timing_manifest)
    try:
        validate_assignment(files, shards)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2

    budget_errors = runtime_budget_errors(shards, timing_manifest)
    if args.check or args.list:
        print_assignment(shards, timing_manifest)
        if budget_errors:
            message = "Runtime safety check failed:\n  " + "\n  ".join(budget_errors)
            if args.check:
                print(message, file=sys.stderr)
                return 1
            print("WARNING: " + message)
        elif args.check:
            print(
                f"Runtime safety check passed: all projections are at or below "
                f"{SAFE_RUNTIME_SECONDS}s."
            )
        return 0

    selected = shards[args.shard - 1]
    relative_files = [str(path.relative_to(PROJECT_ROOT)) for path in selected]
    selected_projection = sum(
        _timing_for(path, timing_manifest)[0] for path in selected
    )
    if budget_errors:
        print("WARNING: " + budget_errors[args.shard - 1], file=sys.stderr)
    print(
        f"Running shard {args.shard}/{SHARD_COUNT}: "
        f"{len(selected)} files, "
        f"{sum(path.stat().st_size for path in selected):,} bytes, "
        f"projected {selected_projection:.1f}s",
        flush=True,
    )
    for relative_path in relative_files:
        print(f"  {relative_path}", flush=True)

    extra_args = list(args.pytest_args)
    if extra_args[:1] == ["--"]:
        extra_args.pop(0)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "scripts.run_test_shard",
        *relative_files,
        "--durations=20",
        "--durations-min=0.25",
        *extra_args,
    ]
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="yonder-shard-timing-") as temp_dir:
            timing_output = Path(temp_dir) / "files.json"
            environment = os.environ.copy()
            environment[_TIMING_OUTPUT_ENV] = str(timing_output)
            returncode = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                check=False,
            ).returncode
            elapsed = time.monotonic() - start
            file_seconds = _read_plugin_timings(timing_output)
            source_sizes = {
                _manifest_key(path): path.stat().st_size for path in selected
            }
            record_timing_manifest(
                path=args.timings,
                file_seconds=file_seconds,
                shard_seconds={args.shard: elapsed},
                source_sizes=source_sizes,
            )
            print(f"Recorded shard {args.shard} timing: {elapsed:.1f}s", flush=True)
            return returncode
    except KeyboardInterrupt:
        print("\nShard interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())