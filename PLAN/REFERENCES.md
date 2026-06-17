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

### FaceForensics (original, 2018)

```bibtex
@article{roessler2018faceforensics,
  author  = {Andreas R{\"o}ssler and Davide Cozzolino and Luisa Verdoliva
             and Christian Riess and Justus Thies and Matthias Nie{\ss}ner},
  title   = {Face{F}orensics: A Large-scale Video Dataset for Forgery Detection in Human Faces},
  journal = {arXiv},
  year    = {2018}
}
```

### FaceForensics++ (ICCV 2019)

```bibtex
@inproceedings{roessler2019faceforensicspp,
  author    = {Andreas R{\"o}ssler and Davide Cozzolino and Luisa Verdoliva
               and Christian Riess and Justus Thies and Matthias Nie{\ss}ner},
  title     = {Face{F}orensics++: Learning to Detect Manipulated Facial Images},
  booktitle = {International Conference on Computer Vision (ICCV)},
  year      = {2019}
}
```

Used as the primary training and evaluation dataset for the Speed Layer (Phase 2.5). c23 compression, Deepfakes manipulation subset. Official split: 720 train / 140 val / 140 test videos. Access via official request form.

### DeepFakes Detection Dataset — Google & JigSaw (2019)

```bibtex
@misc{DDD_GoogleJigSaw2019,
  author = {Dufour, Nicholas and Gully, Andrew and Karlsson, Per and
            Vorbyov, Alexey Victor and Leung, Thomas and Childs, Jeremiah
            and Bregler, Christoph},
  date   = {2019-09},
  title  = {DeepFakes Detection Dataset by Google \& JigSaw}
}
```

Referenced as a cross-dataset generalisation benchmark.

| Dataset | Use in ARDD-TP |
|---|---|
| FaceForensics++ (c23, Deepfakes) | Speed Layer training + evaluation (Phase 2.5) |
| Celeb-DF | Cross-dataset generalisation reference |
| DFDC (Google/JigSaw) | Cross-dataset generalisation reference |

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
