#!/usr/bin/env python3
"""Deterministically partition and run the complete pytest suite.

Shard membership is calculated from the current ``tests/test_*.py`` inventory,
so newly added files are included automatically.  Files are assigned with a
stable longest-processing-time heuristic using their collected test-function
count and source size as a runtime proxy.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
DEFAULT_SHARDS = 6
DEFAULT_TIMEOUT_SECONDS = 270


@dataclass(frozen=True)
class WeightedFile:
    path: Path
    test_count: int
    size_bytes: int

    @property
    def weight(self) -> int:
        # Test count dominates. Source size breaks up files with similarly sized
        # collections but substantially different fixture/setup complexity.
        return max(1, self.test_count) * 10_000 + self.size_bytes


def discover_test_files(tests_dir: Path = TESTS_DIR) -> list[Path]:
    """Return every top-level pytest module in stable path order."""
    return sorted(path.resolve() for path in tests_dir.glob("test_*.py") if path.is_file())


def _test_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def weigh(path: Path) -> WeightedFile:
    return WeightedFile(
        path=path.resolve(),
        test_count=_test_count(path),
        size_bytes=path.stat().st_size,
    )


def assign_shards(files: Sequence[Path], shard_count: int = DEFAULT_SHARDS) -> list[list[WeightedFile]]:
    """Assign each file exactly once using deterministic greedy balancing."""
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")

    weighted = sorted(
        (weigh(path) for path in files),
        key=lambda item: (-item.weight, item.path.as_posix()),
    )
    shards: list[list[WeightedFile]] = [[] for _ in range(shard_count)]
    totals = [0] * shard_count

    for item in weighted:
        target = min(range(shard_count), key=lambda index: (totals[index], index))
        shards[target].append(item)
        totals[target] += item.weight

    for shard in shards:
        shard.sort(key=lambda item: item.path.as_posix())
    return shards


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def check_assignment(shards: Sequence[Sequence[WeightedFile]], discovered: Sequence[Path]) -> None:
    assigned = [item.path for shard in shards for item in shard]
    discovered_resolved = [path.resolve() for path in discovered]
    if len(assigned) != len(set(assigned)):
        duplicates = sorted(_relative(path) for path in assigned if assigned.count(path) > 1)
        raise RuntimeError(f"duplicate test files in shard map: {', '.join(duplicates)}")
    if set(assigned) != set(discovered_resolved):
        missing = sorted(_relative(path) for path in set(discovered_resolved) - set(assigned))
        extra = sorted(_relative(path) for path in set(assigned) - set(discovered_resolved))
        raise RuntimeError(f"incomplete shard map; missing={missing}, extra={extra}")
    if discovered and any(not shard for shard in shards):
        raise RuntimeError("one or more shards are empty")


def print_assignment(shards: Sequence[Sequence[WeightedFile]]) -> None:
    for index, shard in enumerate(shards, start=1):
        tests = sum(item.test_count for item in shard)
        weight = sum(item.weight for item in shard)
        print(f"shard {index}: {len(shard)} files, {tests} test functions, weight {weight}")
        for item in shard:
            print(
                f"  {_relative(item.path)}"
                f"  (tests={item.test_count}, bytes={item.size_bytes}, weight={item.weight})"
            )


def pytest_command(shard: Sequence[WeightedFile], pytest_args: Sequence[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--durations=20",
        *(_relative(item.path) for item in shard),
        *pytest_args,
    ]


def run_shard(
    shard_number: int,
    shards: Sequence[Sequence[WeightedFile]],
    pytest_args: Sequence[str],
    timeout_seconds: int,
    *,
    capture_path: Path | None = None,
) -> int:
    if not 1 <= shard_number <= len(shards):
        raise ValueError(f"shard must be between 1 and {len(shards)}")
    command = pytest_command(shards[shard_number - 1], pytest_args)
    print(f"[shard {shard_number}] {' '.join(command)}", flush=True)

    output = None
    if capture_path is not None:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        output = capture_path.open("w", encoding="utf-8")
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": "0"},
            stdout=output,
            stderr=subprocess.STDOUT if output is not None else None,
            timeout=timeout_seconds,
            check=False,
        )
        return completed.returncode
    except subprocess.TimeoutExpired:
        print(f"[shard {shard_number}] timed out after {timeout_seconds}s", file=sys.stderr)
        return 124
    finally:
        if output is not None:
            output.close()


def _collected_nodeids(paths: Sequence[Path]) -> set[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        *(_relative(path) for path in paths),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise RuntimeError("pytest collection failed")
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }


def verify_collection(shards: Sequence[Sequence[WeightedFile]], discovered: Sequence[Path]) -> None:
    full = _collected_nodeids(discovered)
    combined: set[str] = set()
    total = 0
    for index, shard in enumerate(shards, start=1):
        nodeids = _collected_nodeids([item.path for item in shard])
        overlap = combined & nodeids
        if overlap:
            raise RuntimeError(f"shard {index} duplicates {len(overlap)} collected tests")
        combined.update(nodeids)
        total += len(nodeids)
        print(f"shard {index}: collected {len(nodeids)} tests")
    if combined != full or total != len(full):
        missing = sorted(full - combined)
        extra = sorted(combined - full)
        raise RuntimeError(
            f"collection mismatch: full={len(full)} shards={total} "
            f"missing={missing[:5]} extra={extra[:5]}"
        )
    print(f"collection verified: {len(full)} tests exactly once across {len(shards)} shards")


def run_all(
    shards: Sequence[Sequence[WeightedFile]],
    pytest_args: Sequence[str],
    timeout_seconds: int,
) -> int:
    with tempfile.TemporaryDirectory(prefix="yonder-test-shards-") as temp_dir:
        temp = Path(temp_dir)
        results: dict[int, tuple[int, Path]] = {}
        with ThreadPoolExecutor(max_workers=len(shards)) as pool:
            futures = {
                pool.submit(
                    run_shard,
                    index,
                    shards,
                    pytest_args,
                    timeout_seconds,
                    capture_path=temp / f"shard-{index}.log",
                ): index
                for index in range(1, len(shards) + 1)
            }
            for future in as_completed(futures):
                index = futures[future]
                log_path = temp / f"shard-{index}.log"
                results[index] = (future.result(), log_path)

        failed = False
        for index in range(1, len(shards) + 1):
            returncode, log_path = results[index]
            print(f"\n===== shard {index} (exit {returncode}) =====")
            print(log_path.read_text(encoding="utf-8"), end="")
            failed = failed or returncode != 0
        return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shards",
        type=int,
        default=DEFAULT_SHARDS,
        help=f"number of deterministic shards (default: {DEFAULT_SHARDS})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="print deterministic shard membership")
    subparsers.add_parser("check", help="prove complete, non-overlapping file coverage")
    subparsers.add_parser(
        "verify-collection",
        help="compare collected pytest node IDs for all shards with the complete suite",
    )

    run = subparsers.add_parser("run", help="run one shard")
    run.add_argument("shard", type=int)
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    run.add_argument("pytest_args", nargs=argparse.REMAINDER)

    all_parser = subparsers.add_parser("all", help="run all shards concurrently")
    all_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    all_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    discovered = discover_test_files()
    shards = assign_shards(discovered, args.shards)
    check_assignment(shards, discovered)

    if args.command == "list":
        print_assignment(shards)
        return 0
    if args.command == "check":
        print(
            f"shard map verified: {len(discovered)} files exactly once "
            f"across {len(shards)} shards"
        )
        return 0
    if args.command == "verify-collection":
        verify_collection(shards, discovered)
        return 0
    if args.command == "run":
        return run_shard(args.shard, shards, args.pytest_args, args.timeout)
    if args.command == "all":
        return run_all(shards, args.pytest_args, args.timeout)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())