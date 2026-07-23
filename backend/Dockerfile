FROM python:3.11-slim

# System deps: ffmpeg (transcode + keyframes + audio extract), tesseract (+FR),
# unzip/curl to fetch the Vosk model.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-fra \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Torch CPU-only AVANT le reste : évite d'embarquer les libs CUDA (~plusieurs Go
# inutiles sans GPU). sentence-transformers réutilisera ce torch déjà présent.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the Vosk small French model into the image (~45 MB) so the container
# runs offline. Path is NOT a mounted volume, so it is never shadowed.
ENV VOSK_MODEL_PATH=/opt/models/vosk-fr
RUN mkdir -p /opt/models \
    && curl -fsSL -o /tmp/vosk.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip \
    && unzip -q /tmp/vosk.zip -d /opt/models \
    && mv /opt/models/vosk-model-small-fr-0.22 /opt/models/vosk-fr \
    && rm /tmp/vosk.zip

# Bake the sentence-transformers model into the image so search works offline.
# Multilingual model: required for French content (same 384 dimensions).
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
