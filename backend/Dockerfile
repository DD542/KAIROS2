FROM python:3.11-slim

# System deps: ffmpeg (transcode + keyframes + audio extract), tesseract.
# Plusieurs modèles OCR : lire de l'anglais avec le modèle français dégrade
# nettement le résultat. Le code retombe sur le français si la langue détectée
# n'a pas son paquet.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-fra \
        tesseract-ocr-eng \
        tesseract-ocr-spa \
        tesseract-ocr-deu \
        tesseract-ocr-ita \
        tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pip récent : la version fournie par l'image de base a des CVE connues.
RUN pip install --no-cache-dir --upgrade pip

# Torch CPU-only AVANT le reste : évite d'embarquer les libs CUDA (~plusieurs Go
# inutiles sans GPU). sentence-transformers réutilisera ce torch déjà présent.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modèles embarqués dans l'image pour un fonctionnement hors-ligne et sans
# latence au 1er lancement : faster-whisper (transcription) + sentence-
# transformers multilingue (recherche). HF_HOME centralise le cache.
ENV HF_HOME=/opt/hf
RUN python -c "from faster_whisper import WhisperModel; \
WhisperModel('small', device='cpu', compute_type='int8')"
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
