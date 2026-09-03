#!/usr/bin/env python3
"""Discover, validate, list, and run deterministic pytest file shards."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SHARD_COUNT = 6
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = PROJECT_ROOT / "tests"


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
    files: Sequence[Path], shard_count: int = SHARD_COUNT
) -> list[list[Path]]:
    """Balance files by byte size using deterministic largest-first packing."""
    if shard_count < 1:
        raise ValueError("shard_count must be positive")

    shards: list[list[Path]] = [[] for _ in range(shard_count)]
    weights = [0] * shard_count
    ranked = sorted(files, key=lambda path: (-path.stat().st_size, path.as_posix()))
    for path in ranked:
        shard_index = min(range(shard_count), key=lambda index: (weights[index], index))
        shards[shard_index].append(path)
        weights[shard_index] += path.stat().st_size

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


def print_assignment(shards: Sequence[Sequence[Path]]) -> None:
    total_files = sum(len(shard) for shard in shards)
    print(f"Validated {total_files} test files across {len(shards)} shards.")
    for index, shard in enumerate(shards, start=1):
        weight = sum(path.stat().st_size for path in shard)
        print(f"\nShard {index}: {len(shard)} files, {weight:,} bytes")
        for path in shard:
            print(f"  {path.relative_to(PROJECT_ROOT)}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="verify complete coverage")
    action.add_argument("--list", action="store_true", help="show all shard memberships")
    action.add_argument("--shard", type=int, metavar=f"1-{SHARD_COUNT}", help="run one shard")
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

    shards = assign_shards(files)
    try:
        validate_assignment(files, shards)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.check or args.list:
        print_assignment(shards)
        return 0

    selected = shards[args.shard - 1]
    relative_files = [str(path.relative_to(PROJECT_ROOT)) for path in selected]
    print(
        f"Running shard {args.shard}/{SHARD_COUNT}: "
        f"{len(selected)} files, "
        f"{sum(path.stat().st_size for path in selected):,} bytes",
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
        *relative_files,
        "--durations=20",
        "--durations-min=0.25",
        *extra_args,
    ]
    try:
        return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode
    except KeyboardInterrupt:
        print("\nShard interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())