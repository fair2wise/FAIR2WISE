# Python 3.12 avoids Python 3.14 native ML stack instability seen locally.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KG_RAG_BACKEND=cborg \
    KG_RAG_CBORG_MODEL=lbl/cborg-chat \
    KG_RAG_GRAPH=storage/kg/matkg_qwen3_235b_580papers.json \
    KG_RAG_RETRIEVAL_BACKEND=lexical \
    KG_RAG_LLM_TIMEOUT=120 \
    KG_RAG_CTX_CHARS=6000 \
    KG_RAG_SHOW_BASELINE=0 \
    CBORG_BASE_URL=https://api.cborg.lbl.gov \
    PYSTOW_HOME=.cache/pystow

WORKDIR /app

# Copy requirements first for Docker layer caching.
COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

COPY . .

RUN mkdir -p .cache/pystow storage/knowledge_gaps storage/ontologies

EXPOSE 11435

CMD ["python3", "app/modules/kg_rag_ollama_api.py", "--api"]

LABEL Name="FAIRtoWISE-FORUM-AI" \
      Version="1.0" \
      Description="Materials KG-RAG chat proxy with CBORG/Ollama backend switch"
