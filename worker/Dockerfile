# CPU-only inference worker. No CUDA base image, no GPU deps -- this must run
# on a plain `kind` node container with no device passthrough.
FROM python:3.11-slim

WORKDIR /app

# Torch CPU wheels are much smaller than the CUDA build; pulling from the
# CPU-only index keeps the image lean since we never touch a GPU here.
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

COPY shard_loader.py infer.py ./

# Pre-download the model at build time so pods don't all hit the HF Hub
# simultaneously on startup (N pods cold-starting = N concurrent downloads).
ARG MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
ENV MODEL_NAME=${MODEL_NAME}
RUN python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${MODEL_NAME}')"

# Shard identity, dataset choice, and output location are all env-driven so
# the same image works for every shard -- the k8s Job just varies SHARD_INDEX.
ENV DATASET_NAME=ag_news
ENV DATASET_SPLIT=train
ENV TEXT_COLUMN=text
ENV OUTPUT_PATH=/results/shard_result.json

CMD ["python3", "infer.py"]
