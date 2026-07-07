# Product Requirements Document

**Project:** Autonomous Real-Time Deepfake Detection & Telemetry Pipeline (ARDD-TP)
**Version:** 1.0.0

## 1. Summary

ARDD-TP is a real-time media verification system. It processes live video streams, evaluates them for AI-generated manipulation using deep learning and RAG-based context checking, and presents actionable telemetry via a live dashboard.

---

## 2. Objectives

- Low-latency anomaly detection on live data streams with an end-to-end SLA of **200ms per frame**.
- Combined structural image analysis with sequential contextual verification.
- Full observability of model performance and drift over time.
- Responsive, state-driven frontend for compliance monitoring.

---

## 3. Key Features (v1.0.0)

- **Stream Ingestion:** Ingest Gateway decodes live video (RTSP/HTTP), extracts frames, and publishes them to Kafka.
- **Dual-Branch Vision Engine:** Deepfake classification using spatial (EfficientNet) and frequency (FFT) domain analysis with a weighted combined score.
- **Contextual Auditor:** LLM-powered verification against known threat signatures, conditioned on the Vision score.
- **Aggregation Service:** Merges vision and RAG results into a single canonical payload; falls back to Vision-only if RAG exceeds 150ms.
- **Telemetry Dashboard:** Live tracking of system health, inference scores, and processing latency.

---

## 4. Current Operational Behaviours

These are active requirements in v1.0.0:

- **Graceful Degradation:** If stream throughput exceeds processing capacity, frame extraction dynamically downsamples from 30 FPS to 5 FPS to maintain real-time telemetry within the 200ms SLA.
- **RAG Timeout Fallback:** If the RAG Context Agent exceeds 100ms, the Aggregation Service resolves using the Vision score alone (`audit_verdict: "UNKNOWN"`).
- **Threat Escalation:** If `final_score > 0.90` for five consecutive frames, a high-priority webhook alert is triggered and the stream segment is archived for manual review.
- **Drift Detection:** If the rolling average confidence drops below 60%, the model is flagged for retraining.
- **Face Alignment Bypass:** If MTCNN fails, inference is skipped and a neutral score (0.5) is returned.

---

## 5. Future States

> Items below are **not** part of the v1.0.0 implementation. See `ROADMAP.md` for phasing.

- **Graph Threat Intelligence DB (Phase 3):** Link recurring synthetic identities across multiple independent streams.
- **Automated Retraining (Phase 3):** Trigger fine-tuning automatically when drift is detected; deploy new weights without downtime.
- **Role-Based Dashboard Access (Phase 4):** Auth layer for compliance environments.
- **Stream Segment Archival (Phase 4):** Persist flagged segments to object storage for audit trail.
- **Webhook Integrations (Phase 4):** Connect threat escalation to Slack, PagerDuty, or SIEM.
