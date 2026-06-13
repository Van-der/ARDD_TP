# References

Models, datasets, and libraries used or referenced in ARDD-TP.

---

## Models

### Temporal Service — ResNext50+LSTM

```bibtex
@misc{namandhakad,
  author       = {Naman, Dhakad},
  title        = {Deep-fake-detection: Advanced Engine with ResNext50 + LSTM},
  year         = {2025},
  publisher    = {Hugging Face},
  howpublished = {\url{https://huggingface.co/Naman712/Deep-fake-detection}},
}
```

Used as the Batch Layer sequence model in Phase 2. Architecture: ResNext50 backbone + LSTM head + linear classifier. Input: `[1, 20, 3, 112, 112]` at 112×112 px, ImageNet-normalised. Reported accuracy: 87% on 20-frame evaluation set.

Weights file: `model_87_acc_20_frames_final_data.pt`
Local cache: `~/.cache/huggingface/hub/models--Naman712--Deep-fake-detection/`

---

## Datasets

| Dataset | Use |
|---|---|
| FaceForensics++ | Standard benchmark for deepfake detection evaluation |
| Celeb-DF | Cross-dataset generalisation reference |
| DFDC (DeepFake Detection Challenge) | Temporal model pre-training reference |

---

## Key Libraries

| Library | Version | Role |
|---|---|---|
| `torch` / `torchvision` | 2.x | Vision Service inference, Temporal Service inference |
| `facenet-pytorch` | 2.6.x | MTCNN face alignment (Vision Service) |
| `aiokafka` | 0.x | Async Kafka consumers (Aggregation + Temporal Service) |
| `langchain` / `langchain-community` | 0.x | RAG pipeline (RAG Agent) |
| `sentence-transformers` | 2.x | Semantic embeddings for FAISS threat signature search (Phase 2) |
| `faiss-cpu` | 1.x | Vector similarity search (RAG Agent) |
| `mlflow` | 2.x | Experiment tracking and drift detection |
