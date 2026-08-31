from __future__ import annotations

import asyncio

import pytest

from threvo_actions.canonical import canonicalize_v1
from threvo_actions.conformance import (
    BenchmarkResult,
    ConformanceError,
    PerformanceProfile,
    assert_performance_profile,
    benchmark_async,
    benchmark_async_concurrent,
    benchmark_sync,
)


def test_four_kib_canonicalization_profile_reports_percentiles() -> None:
    document = {"action": "synthetic", "private_payload": "x" * 4096}
    profile = PerformanceProfile(
        name="canonicalization-4-kib",
        max_p99_ms=20,
        min_iterations=100,
    )

    result = benchmark_sync(lambda: canonicalize_v1(document), profile=profile)

    assert result.iterations == 100
    assert result.p50_ms <= result.p95_ms <= result.p99_ms


def test_largest_bulk_canonicalization_profile_is_measured_separately() -> None:
    document = {
        "action": "synthetic-bulk",
        "items": [{"item_reference": f"item:{index}", "version": index} for index in range(500)],
    }
    profile = PerformanceProfile(
        name="canonicalization-500-items",
        max_p99_ms=100,
        min_iterations=100,
    )

    result = benchmark_sync(lambda: canonicalize_v1(document), profile=profile)

    assert result.iterations == 100
    assert result.p50_ms <= result.p95_ms <= result.p99_ms


def test_async_harness_measures_in_process_orchestration_without_provider_io() -> None:
    async def scenario() -> None:
        calls = 0

        async def operation() -> None:
            nonlocal calls
            calls += 1

        profile = PerformanceProfile(
            name="async-orchestration-baseline",
            max_p99_ms=10,
            min_iterations=100,
        )
        result = await benchmark_async(operation, profile=profile)

        assert calls == 110
        assert result.p50_ms <= result.p95_ms <= result.p99_ms

    asyncio.run(scenario())


def test_profile_failure_is_stable_and_does_not_report_measured_values() -> None:
    profile = PerformanceProfile(name="strict", max_p99_ms=1, min_iterations=1)
    result = BenchmarkResult(
        profile="strict",
        iterations=1,
        p50_ms=2,
        p95_ms=2,
        p99_ms=2,
    )

    with pytest.raises(ConformanceError, match="performance_p99"):
        assert_performance_profile(result, profile)


def test_concurrent_harness_reports_per_operation_latency() -> None:
    async def scenario() -> None:
        seen: set[int] = set()

        async def operation(index: int) -> None:
            seen.add(index)

        profile = PerformanceProfile(
            name="concurrent-baseline",
            max_p99_ms=10,
            min_iterations=100,
        )
        result = await benchmark_async_concurrent(
            operation,
            profile=profile,
            concurrency=20,
            batches=5,
            warmup_batches=1,
        )

        assert result.iterations == 100
        assert len(seen) == 120

    asyncio.run(scenario())
