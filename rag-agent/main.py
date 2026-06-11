import os
import base64
import time
import hashlib
import json
import logging
from typing import List, Optional, Tuple
from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel, Field

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI()

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
MOCK_LLM = os.getenv("MOCK_LLM", "true").lower() == "true"

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

# Custom Deterministic Embedding model
from langchain_core.embeddings import Embeddings

class SimpleHashEmbeddings(Embeddings):
    def __init__(self, dimension: int = 128):
        self.dimension = dimension

    def _embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        words = text.lower().replace(",", " ").replace(".", " ").split()
        if not words:
            return vec
        for word in words:
            h = int(hashlib.md5(word.encode('utf-8')).hexdigest(), 16)
            idx = h % self.dimension
            vec[idx] += 1.0
        norm = sum(x*x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)

# Initialize vector store
vector_store = None
vector_store_initialized = False

def init_vector_store():
    global vector_store, vector_store_initialized
    try:
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document
        
        embeddings = SimpleHashEmbeddings()
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
                    "severity": sig["severity"]
                }
            )
            documents.append(doc)
            
        vector_store = FAISS.from_documents(documents, embeddings)
        vector_store_initialized = True
        logger.info("FAISS vector store successfully initialized with threat signatures.")
    except Exception as e:
        logger.error(f"Failed to initialize FAISS vector store: {e}")
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

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return api_key

def map_score_to_query(score: float) -> str:
    """Map deepfake score to a semantic query to search FAISS."""
    if score >= 0.8:
        return "high confidence deepfake face swap artifacts visible blending temporal flicker boundary blending"
    elif score >= 0.5:
        return "medium deepfake score irregular frequency domain patterns spectral anomalies compression artefact"
    else:
        return "low deepfake score clean face authentic real frame eye reflection mismatch"

async def generate_verdict_via_llm(score: float, sig_label: str, sig_desc: str, sig_sev: str) -> Tuple[str, float]:
    """Generates the verdict by calling the Ollama service."""
    if MOCK_LLM:
        # Simulate local check: High score and matching signature is a FAIL
        if score >= 0.5:
            return "FAIL", min(0.95, float(score + 0.1))
        return "UNKNOWN", 0.0

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
        async with httpx.AsyncClient(timeout=0.1) as client:  # Enforce sub-100ms LLM budget if possible
            response = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": "mistral",
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
                return verdict, confidence
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Malformed LLM response: '{llm_text}', error: {e}")
                return "UNKNOWN", 0.0
                
    except httpx.RequestError as e:
        logger.error(f"Ollama connection failed: {e}")
        # Treated as timeout by Aggregation Service
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
    try:
        # Returns List[Tuple[Document, float]]
        results = vector_store.similarity_search_with_score(query, k=1)
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        raise HTTPException(status_code=503, detail="Vector store search failed")

    verdict = "UNKNOWN"
    matched_sig = None
    confidence = 0.0

    if results:
        doc, l2_dist = results[0]
        # Convert L2 distance to similarity score
        # For normalized vectors: dist_sq = 2 - 2*cos_sim => cos_sim = 1 - dist_sq/2
        similarity_score = 1.0 - (l2_dist / 2.0)
        
        logger.info(f"Matched signature: {doc.metadata['label']} with similarity: {similarity_score:.4f}")
        
        # Threshold check — SCHEMA.md §9.2 requires similarity_score >= 0.75
        if similarity_score >= 0.75:
            matched_sig = doc.metadata["label"]
            # Call Ollama/Mistral (or mock) to generate verdict
            verdict, confidence = await generate_verdict_via_llm(
                req.deepfake_score,
                doc.metadata["label"],
                doc.page_content,
                doc.metadata["severity"]
            )
            
    return AuditResult(
        stream_id=req.stream_id,
        frame_index=req.frame_index,
        audit_verdict=verdict,
        matched_signature=matched_sig,
        confidence=confidence
    )

@app.get("/health")
async def health():
    if not vector_store_initialized:
        raise HTTPException(status_code=503, detail="Vector store unavailable")
    return {
        "status": "ok",
        "service": "rag-agent",
        "uptime_s": 0
    }
