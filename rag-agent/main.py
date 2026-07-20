import os
import base64
import time
import json
import logging
from typing import List, Optional, Tuple
from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI()

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "tinyllama")
MOCK_LLM = os.getenv("MOCK_LLM", "true").lower() == "true"
PROFILE = os.getenv("PROFILE", "false").lower() == "true"
START_TIME = time.time()

# OpenTelemetry tracing (M8) — local Jaeger via otel-collector, no cloud APM.
_otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if _otel_endpoint:
    _provider = TracerProvider(resource=Resource.create({"service.name": "rag-agent"}))
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint, insecure=True)))
    trace.set_tracer_provider(_provider)
    FastAPIInstrumentor.instrument_app(app)
tracer = trace.get_tracer(__name__)

# Predefined Threat Signatures from SCHEMA.md
THREAT_SIGNATURES = [
    {
        "signature_id": "8a5cf748-0d12-4cfc-b4db-ea5a76c6b4b4",
        "label": "FaceSwap-v2-GAN",
        "description": "High confidence deepfake face swap artifacts visible blending boundary blending temporal flicker texture inconsistency.",
        "artefact_tags": ["boundary_blending", "temporal_flicker", "texture_inconsistency"],
        "source": "MANUAL",
        "severity": "HIGH",
        "active": True
    },
    {
        "signature_id": "4cf58a5c-f748-0d12-b4db-ea5a76c6b4b5",
        "label": "SpectralAnomaly-FFT",
        "description": "Medium deepfake score irregular frequency domain patterns spectral anomalies compression artefact GAN post-processing.",
        "artefact_tags": ["spectral_anomaly", "compression_artefact"],
        "source": "AUTO_DETECTED",
        "severity": "MEDIUM",
        "active": True
    },
    {
        "signature_id": "d12e4c5a-8a5c-4cfc-b4db-ea5a76c6b4b6",
        "label": "EyeReflection-Mismatch",
        "description": "Low deepfake score clean face authentic real frame eye reflection mismatch texture inconsistency corneal reflections.",
        "artefact_tags": ["eye_reflection_mismatch", "texture_inconsistency"],
        "source": "IMPORTED",
        "severity": "HIGH",
        "active": True
    }
]

# Removed SimpleHashEmbeddings

# Initialize vector store
vector_store = None
vector_store_initialized = False

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma")

def init_vector_store():
    global vector_store, vector_store_initialized
    try:
        from langchain_community.vectorstores import Chroma
        from langchain_core.documents import Document
        from langchain_huggingface import HuggingFaceEmbeddings
        from chromadb.config import Settings as ChromaSettings

        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        documents = []
        for sig in THREAT_SIGNATURES:
            if not sig["active"]:
                continue
            # Embed description + tags
            content = f"{sig['description']} {' '.join(sig['artefact_tags'])}"
            doc = Document(
                page_content=content,
                metadata={
                    "signature_id": sig["signature_id"],
                    "label": sig["label"],
                    "severity": sig["severity"],
                    # Chroma's metadata store only accepts scalar values (str/int/
                    # float/bool) — unlike FAISS's in-memory dict, it can't hold a
                    # raw list. Comma-joined here; split back to a list at the
                    # read site (audit()) before passing to generate_verdict_via_llm.
                    "artefact_tags": ",".join(sig["artefact_tags"]),
                }
            )
            documents.append(doc)

        # hnsw:space="l2" must be pinned explicitly — Chroma's default distance
        # metric for this embedding function would otherwise be cosine, which
        # would silently break the existing `1.0 - (l2_dist / 2.0)` conversion
        # and the >=0.75 match threshold in audit() below.
        vector_store = Chroma.from_documents(
            documents, embeddings,
            persist_directory=CHROMA_PERSIST_DIR,
            collection_metadata={"hnsw:space": "l2"},
            client_settings=ChromaSettings(anonymized_telemetry=False),
        )
        vector_store_initialized = True
        logger.info("Chroma vector store successfully initialized with threat signatures.")
    except Exception as e:
        logger.error(f"Failed to initialize Chroma vector store: {e}")
        vector_store_initialized = False

# Try to initialize at import
init_vector_store()

# Schema definitions
class AuditRequest(BaseModel):
    stream_id: str
    frame_index: int
    deepfake_score: float = Field(..., ge=0.0, le=1.0)

class AuditResult(BaseModel):
    stream_id: str
    frame_index: int
    audit_verdict: str
    matched_signature: Optional[str]
    confidence: float
    summary: str

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return api_key

def map_score_to_query(score: float) -> str:
    """Map deepfake score to a semantic query to search the vector store."""
    if score >= 0.8:
        return "high confidence deepfake face swap artifacts visible blending temporal flicker boundary blending"
    elif score >= 0.5:
        return "medium deepfake score irregular frequency domain patterns spectral anomalies compression artefact"
    else:
        return "low deepfake score clean face authentic real frame eye reflection mismatch"

async def generate_verdict_via_llm(
    score: float, sig_label: str, sig_desc: str, sig_sev: str,
    sig_tags: Optional[List[str]] = None,
) -> Tuple[str, float, str]:
    """Generates the verdict by calling the Ollama service."""
    if MOCK_LLM:
        sev = sig_sev.capitalize()
        tags_str = ", ".join(sig_tags).replace("_", " ") if sig_tags else "unknown artifacts"
        if score >= 0.80:
            summary = (f"High-confidence {sig_label} detected ({sev} severity). "
                       f"Spatial artifacts: {tags_str}.")
            return "FAIL", min(0.95, score + 0.05), summary
        if score >= 0.50:
            summary = (f"Moderate deepfake indicators — {sig_label} pattern matched "
                       f"({sev} severity, score {score:.0%}). Artifacts: {tags_str}.")
            return "FAIL", min(0.85, score + 0.10), summary
        if score >= 0.30:
            summary = (f"Ambiguous frame — low-confidence {sig_label} signals "
                       f"below alert threshold (score {score:.0%}).")
            return "UNKNOWN", 0.0, summary
        summary = "No significant deepfake artifacts detected in this frame."
        return "PASS", round(1.0 - score, 4), summary

    import httpx
    prompt = f"""You are a deepfake security auditor.
A vision model detected a potential deepfake with score: {score}.
The closest threat signature matched is:
Label: {sig_label}
Description: {sig_desc}
Severity: {sig_sev}

Analyze if this is a deepfake threat. You must respond ONLY with a JSON object in this exact format:
{{"verdict": "FAIL", "confidence": 0.92}}
The verdict must be FAIL, PASS, or UNKNOWN.
"""
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                }
            )
            if response.status_code != 200:
                raise HTTPException(status_code=503, detail="Ollama service returned error status")
            
            res_data = response.json()
            llm_text = res_data.get("response", "").strip()
            # Parse response
            try:
                verdict_data = json.loads(llm_text)
                verdict = verdict_data.get("verdict", "UNKNOWN")
                confidence = float(verdict_data.get("confidence", 0.0))
                if verdict not in ["PASS", "FAIL", "UNKNOWN"]:
                    verdict = "UNKNOWN"
                return verdict, confidence, f"LLM verdict: {verdict} (confidence {confidence:.0%})."
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Malformed LLM response: '{llm_text}', error: {e}")
                return "UNKNOWN", 0.0, "LLM response could not be parsed — verdict inconclusive."

    except httpx.RequestError as e:
        logger.error(f"Ollama connection failed: {e}")
        raise HTTPException(status_code=503, detail="Ollama service unavailable")

@app.post("/audit", response_model=AuditResult, dependencies=[Depends(verify_api_key)])
async def audit(req: AuditRequest):
    if not vector_store_initialized:
        # Try to reinitialize
        init_vector_store()
        if not vector_store_initialized:
            raise HTTPException(status_code=503, detail="Vector store unavailable")
            
    # Search vector store using mapped score query
    query = map_score_to_query(req.deepfake_score)
    t0 = time.perf_counter()
    try:
        with tracer.start_as_current_span("vector_search"):
            # Returns List[Tuple[Document, float]]
            results = vector_store.similarity_search_with_score(query, k=1)
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        raise HTTPException(status_code=503, detail="Vector store search failed")
    t_search = time.perf_counter()

    verdict = "UNKNOWN"
    matched_sig = None
    confidence = 0.0
    summary = "No signature matched — verdict inconclusive."

    if results:
        doc, l2_dist = results[0]
        similarity_score = 1.0 - (l2_dist / 2.0)
        logger.info(f"Matched signature: {doc.metadata['label']} with similarity: {similarity_score:.4f}")

        if similarity_score >= 0.75:
            matched_sig = doc.metadata["label"]
            tags = doc.metadata.get("artefact_tags")
            with tracer.start_as_current_span("generate_verdict_via_llm"):
                verdict, confidence, summary = await generate_verdict_via_llm(
                    req.deepfake_score,
                    doc.metadata["label"],
                    doc.page_content,
                    doc.metadata["severity"],
                    tags.split(",") if tags else None,
                )

    if PROFILE:
        t_llm = time.perf_counter()
        logger.info(
            "PROFILE rag stream=%s frame=%s mock_llm=%s search_ms=%.1f llm_ms=%.1f",
            req.stream_id, req.frame_index, MOCK_LLM,
            (t_search - t0) * 1000, (t_llm - t_search) * 1000,
        )

    return AuditResult(
        stream_id=req.stream_id,
        frame_index=req.frame_index,
        audit_verdict=verdict,
        matched_signature=matched_sig,
        confidence=confidence,
        summary=summary,
    )

@app.get("/health")
async def health():
    if not vector_store_initialized:
        raise HTTPException(status_code=503, detail="Vector store unavailable")
    return {
        "status": "ok",
        "service": "rag-agent",
        "uptime_s": int(time.time() - START_TIME)
    }
