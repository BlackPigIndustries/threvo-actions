# Runtime overhead benchmark

Status: local reference measurement, not a universal deployment result.

The benchmark separates canonicalization from in-process proposal preparation
for two stable synthetic profiles: a 4 KiB private payload and a 500-item bulk
snapshot. Preparation includes strict model validation, canonicalization,
proposal-scoped commitment work, protection, receipt construction, and the
`MemoryActionStore`. It excludes database, network, model-provider, payment
provider, key-service, and other host/external I/O.

Run it from the repository root:

```bash
uv run python -m benchmarks.runtime_overhead
```

The command warms one batch, measures ten batches of 100 concurrent proposals,
reports 1,000 per-operation samples for each preparation profile, and exits
non-zero if a configured p95 or p99 boundary is exceeded. Canonicalization is
measured separately over 1,000 iterations. Inputs, concurrency, thresholds, and
the fixed no-I/O host are versioned in
[`benchmarks/runtime_overhead.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/benchmarks/runtime_overhead.py).

## Reference result

Measured on 29 August 2026 with Python 3.13.5 on
`macOS-26.4.1-arm64-arm-64bit-Mach-O`:

| Profile | Samples | p50 | p95 | p99 | Enforced boundary |
| --- | ---: | ---: | ---: | ---: | --- |
| Canonicalization, 4 KiB | 1,000 | 0.008 ms | 0.009 ms | 0.010 ms | p95 ≤ 5 ms; p99 ≤ 10 ms |
| Preparation, 4 KiB, 100 concurrent | 1,000 | 0.080 ms | 0.101 ms | 0.193 ms | p95 ≤ 10 ms; p99 ≤ 25 ms |
| Canonicalization, 500 items | 1,000 | 0.035 ms | 0.037 ms | 0.062 ms | p95 ≤ 10 ms; p99 ≤ 20 ms |
| Preparation, 500 items, 100 concurrent | 1,000 | 0.595 ms | 0.730 ms | 0.975 ms | p95 ≤ 25 ms; p99 ≤ 50 ms |

The 4 KiB preparation profile passes the product criterion of less than 10 ms
p95 under 100 concurrent in-process actions. The 500-item profile is kept
separate so a small happy-path payload cannot conceal bulk serialization cost.

These measurements do not predict production latency. A real PostgreSQL store,
cryptographic key service, authorization database, ERP, PSP, bank rail, or
verifier will dominate this synthetic path and must be measured independently.
A future breach of the in-process profile triggers profiling; it does not by
itself justify a Rust rewrite.
