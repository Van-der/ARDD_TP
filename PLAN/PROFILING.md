# Per-Frame Pipeline Profiling (M0)

> Measured 2026-07-10 against the live Docker Compose stack, using `video_feeder.py --mode eval --max-videos 2 --max-frames 100 --fps 5` (400 frames: 200 real + 200 fake) with temporary `PROFILE=true` timing instrumentation in `vision-service/main.py`, `aggregation-service/main.py`, and `rag-agent/main.py`.

## Purpose

`TaskTo.md` asserted the pipeline runs at "~1.5 FPS... Vision+RAG latency ≈ 500ms/frame" and that a gRPC migration was required to reach 10+ FPS for the full FF++ benchmark. That figure had no profiling breakdown behind it. This pass measures where time actually goes, split by stage, in both `MOCK_LLM=true` (the docker-compose default) and `MOCK_LLM=false` (real Ollama/tinyllama) configurations.

## Results — `MOCK_LLM=true` (400 frames)

| Stage | Median | p95 | Mean | Share of vision total |
|---|---|---|---|---|
| JPEG decode + color convert | 4.9ms | 8.1ms | 4.2ms | 4% |
| MTCNN face detection | **89.2ms** | 152.1ms | 89.6ms | **65%** |
| Spatial branch (EfficientNet-B4 forward) | 24.9ms | 57.7ms | 31.9ms | 18% |
| FFT MLP branch | 9.3ms | 13.1ms | 10.0ms | 7% |
| **vision-service total (server-side)** | **138.0ms** | 210.0ms | 135.7ms | 100% |
| Aggregation→Vision round trip (as observed by aggregation-service) | 148.8ms | 227.8ms | 158.9ms | — |
| **REST/serialization overhead (round trip − server latency)** | **9.8ms** | 16.1ms | 23.2ms | **~7% of round trip** |
| RAG round trip (mocked LLM) | 14.5ms | 73.7ms | 24.2ms | — |
| — of which vector search | 6.8ms | 63.1ms | 13.8ms | — |
| — of which LLM call (mocked) | 0.1ms | 0.2ms | 0.2ms | — |

**Typical per-frame pipeline latency with `MOCK_LLM=true`: ~150-160ms** (vision round trip + mocked RAG round trip) — well under the ~500ms historical figure, and nowhere close to needing gRPC to hit reasonable throughput.

## Results — `MOCK_LLM=false` (real Ollama/tinyllama, 168 LLM-invoking samples captured before backlog outpaced the eval window)

| Stage | Median | p95 | Max |
|---|---|---|---|
| Vector search (unchanged) | 6.7ms | 48.3ms | — |
| **Real LLM call (tinyllama, when a signature matches)** | **964.0ms** | 1753.0ms | **5792.1ms** |
| RAG round trip as observed by aggregation-service (all frames, matched + unmatched) | 18.5ms | 1222.0ms | 5808.9ms |

**Critical finding:** when `MOCK_LLM=false`, the real LLM verdict-generation call alone costs **~1 second at the median and up to ~5.8 seconds at the tail** — over **100x** the ~10ms REST overhead measured above. This single call, awaited synchronously inside `process_frame_payload()` before the frame's processing completes, is the dominant cost by a wide margin whenever it fires (i.e., whenever `similarity_score >= 0.75` matches a threat signature).

This is directly visible in the eval run itself: with `MOCK_LLM=false`, the pipeline could not fully drain 400 frames within the eval script's 90s drain window — only the 2 real streams were scored before the collector gave up; the 2 fake streams (which, expectedly, match threat signatures far more often, triggering many more real LLM calls) were still backlogged in Kafka. No errors occurred (0 timeouts, 0 failures — `RAG_TIMEOUT=15s` comfortably covers even the 5.8s worst case) — the pipeline is correct, just slow whenever real inference is required.

## Go/No-Go Recommendation: **NO-GO on gRPC migration**

REST/serialization overhead is a consistent ~7-10ms across both configurations — a single-digit percentage of the vision round trip and completely negligible next to real LLM latency. Migrating the aggregation↔vision hop to gRPC would recover at most that ~10ms per frame. That is not worth the engineering cost of a protocol migration (M1 as originally scoped), and it does nothing for the actual bottleneck.

**Revised scope for M1**, informed by this data:

1. **Skip the full gRPC migration.** Not justified by measurement.
2. **Small, cheap win (optional):** drop base64 encoding for the frame payload in favor of raw bytes over the existing REST endpoint. This is a minor optimization (the ~10ms REST overhead already includes base64's ~33% size inflation), worth doing opportunistically but not the headline item.
3. **Real target: decouple the RAG call from the per-frame blocking path.** `aggregation-service/main.py`'s `process_frame_payload()` currently `await`s `call_rag()` synchronously before the frame is considered processed, and `start_frames_consumer()`'s `async for msg in consumer: await process_frame_payload(...)` loop is fully serial — so every signature-matching frame stalls the *entire* per-stream consumer loop for ~1-6 seconds. This is the actual throughput ceiling when running with a real LLM, not transport overhead. The fix: broadcast the vision score immediately (as today), then fire the RAG call via `asyncio.create_task()` (the exact pattern already used for webhook delivery at `_deliver_webhook()`, line ~501) and broadcast a follow-up `audit_update` WebSocket event when the verdict resolves, instead of blocking the frame-processing loop on it.
4. MTCNN face detection (65% of vision-service's own latency) is the largest single cost in the `MOCK_LLM=true`/vision-only path. Not a REST/gRPC concern; a future optimization target (e.g. batching, a lighter detector) if vision-service throughput itself becomes the bottleneck, but out of scope for M1.
5. The full FF++ 140/140 benchmark (`run_benchmark.py`) should be run with `MOCK_LLM=true` for realistic throughput expectations (~150ms/frame achievable) — running it with real LLM inference would take dramatically longer than the ~23h estimate in `TaskTo.md`, since that estimate assumed REST was the bottleneck, not ~1s-per-matched-frame real LLM calls.

## M1 Verification (2026-07-10, post-fix)

Implemented: `aggregation-service/main.py`'s `start_frames_consumer()` no longer `await`s `process_frame_payload()` serially in the Kafka consumer loop. Each message now dispatches via `asyncio.create_task(_process_frame_locked(payload))`, where `_process_frame_locked` holds a per-`stream_id` `asyncio.Lock` — frames within the same stream still process strictly in order (preserving `alert_counters`/`drift_history` correctness), but different streams no longer serialize behind each other's real-LLM latency. `process_frame_payload()` itself, and the test-only `POST /aggregate` endpoint that calls it synchronously, are unchanged — all 30 existing aggregation-service tests pass unmodified.

Re-ran the identical `MOCK_LLM=false` eval pass (400 frames, 2 real + 2 fake streams) used to establish the original bottleneck:

| | Before fix | After fix |
|---|---|---|
| Streams scored within the 90s drain window | 2 of 4 | **4 of 4** |
| Accuracy / Precision / Recall / AUC | 1.0 / 0.0 / 0.0 / nan (fake streams incomplete) | **1.0 / 1.0 / 1.0 / 1.0** |

The 2 fake streams — which trigger real LLM calls far more often since they match threat signatures more frequently — were previously starved behind the real streams' processing. With per-stream (not global) serialization, all 4 streams completed within the same drain window. This directly confirms the fix: real LLM latency on one stream no longer blocks unrelated streams.

## Doc updates arising from this finding

- `TaskTo.md`'s "~500ms/frame... gRPC migration to reach 10+ FPS" framing is superseded by this report.
- `PLAN/ROADMAP.md` Phase 3 "gRPC transport" row should be marked as evaluated-and-declined, with a pointer to this file.
