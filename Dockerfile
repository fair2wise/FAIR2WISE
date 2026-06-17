FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && \
    mkdir -p /app/storage && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 11435

CMD ["python", "app/modules/kg_rag_ollama_api.py", "--api", "--graph", "storage/kg/matkg_qwen3_235b_580papers.json"]
