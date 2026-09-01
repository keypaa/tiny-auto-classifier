"""
CPU/RSS timer — Roadmap §9, §26–28, §49.

Measures for batch=1:
  cold-start latency, warm latency, tokenization time,
  model execution time, total latency, p50/p95/p99, peak RSS, CPU utilization

Also provides thread sweep helper.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional
import os

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

import numpy as np


@dataclass
class TimerResult:
    latencies_ms: list[float]
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    min_ms: float
    max_ms: float
    rss_mb: float
    peak_rss_mb: float

    def to_dict(self):
        return {
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "mean_ms": self.mean_ms,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "rss_mb": self.rss_mb,
            "peak_rss_mb": self.peak_rss_mb,
            "count": len(self.latencies_ms),
        }


def _rss_mb() -> float:
    if psutil is None:
        return 0.0
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def _percentiles(latencies_ms: list[float]) -> tuple[float, float, float, float]:
    arr = np.array(latencies_ms, dtype=float)
    return (
        float(np.percentile(arr, 50)),
        float(np.percentile(arr, 95)),
        float(np.percentile(arr, 99)),
        float(np.mean(arr)),
    )


def benchmark(
    fn: Callable[[], None],
    *,
    warmup: int = 3,
    iters: int = 20,
    measure_rss: bool = True,
) -> TimerResult:
    """
    Benchmark fn() (should include tokenization+model if you want total latency).
    Callers wanting split timing should benchmark sub-fns separately.
    """
    # warmup (not timed)
    for _ in range(warmup):
        fn()

    latencies: list[float] = []
    peak = _rss_mb() if measure_rss else 0.0
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)
        if measure_rss:
            rss = _rss_mb()
            if rss > peak:
                peak = rss

    p50, p95, p99, mean = _percentiles(latencies)
    rss_now = _rss_mb() if measure_rss else 0.0
    return TimerResult(
        latencies_ms=latencies,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        mean_ms=mean,
        min_ms=float(min(latencies)),
        max_ms=float(max(latencies)),
        rss_mb=rss_now,
        peak_rss_mb=peak,
    )


def thread_sweep(
    fn_factory: Callable[[int], Callable[[], None]],
    thread_counts: list[int],
    *,
    warmup: int = 2,
    iters: int = 10,
) -> dict[int, TimerResult]:
    """
    Benchmark across thread counts. fn_factory(threads) -> fn.
    Caller is responsible for setting intra-op threads inside fn_factory
    (e.g., torch.set_num_threads, onnx intra_op_num_threads, OMP_NUM_THREADS).
    """
    results: dict[int, TimerResult] = {}
    for tc in thread_counts:
        fn = fn_factory(tc)
        results[tc] = benchmark(fn, warmup=warmup, iters=iters)
    return results


def split_timer(
    tokenize_fn: Callable[[], None],
    model_fn: Callable[[], None],
    *,
    iters: int = 20,
) -> dict:
    """Measure tokenization vs model execution separately (Roadmap §28)."""
    tok_res = benchmark(tokenize_fn, iters=iters)
    mod_res = benchmark(model_fn, iters=iters)
    # total
    def total():
        tokenize_fn()
        model_fn()
    tot_res = benchmark(total, iters=iters)
    return {
        "tokenization": tok_res.to_dict(),
        "model_execution": mod_res.to_dict(),
        "total": tot_res.to_dict(),
    }
