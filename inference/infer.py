"""
Runs CPU embedding inference over a shard and writes per-example results +
timing to a JSON file. This is the piece that will later run inside a k8s
pod; today (Milestone 2) it runs as a plain local process so the ML logic
gets validated before k8s is involved at all.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict

from sentence_transformers import SentenceTransformer

from shard_loader import ShardConfig, load_shard

MODEL_NAME = os.environ.get("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")


def run(config: ShardConfig, output_path: str, model_name: str = MODEL_NAME) -> dict:
    shard = load_shard(config)
    texts = shard[config.text_column]

    load_start = time.perf_counter()
    model = SentenceTransformer(model_name, device="cpu")
    model_load_seconds = time.perf_counter() - load_start

    infer_start = time.perf_counter()
    embeddings = model.encode(texts, show_progress_bar=False, batch_size=32)
    infer_seconds = time.perf_counter() - infer_start

    n = len(texts)
    result = {
        "shard_index": config.shard_index,
        "shard_count": config.shard_count,
        "dataset_name": config.dataset_name,
        "model_name": model_name,
        "num_examples": n,
        "model_load_seconds": round(model_load_seconds, 4),
        "inference_seconds": round(infer_seconds, 4),
        "examples_per_second": round(n / infer_seconds, 2) if infer_seconds > 0 else None,
        # Store embedding dim + a couple of sample vectors truncated, not the
        # full matrix -- Postgres row size and log noise both matter once
        # this scales to real shard counts.
        "embedding_dim": int(embeddings.shape[1]),
        "sample": [
            {"text": texts[i][:200], "embedding_preview": embeddings[i][:8].tolist()}
            for i in range(min(3, n))
        ],
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


if __name__ == "__main__":
    cfg = ShardConfig.from_env() if "SHARD_INDEX" in os.environ else ShardConfig(
        dataset_name=os.environ.get("DATASET_NAME", "ag_news"),
        split=os.environ.get("DATASET_SPLIT", "train"),
        text_column=os.environ.get("TEXT_COLUMN", "text"),
        shard_index=int(os.environ.get("SHARD_INDEX", "0")),
        shard_count=int(os.environ.get("SHARD_COUNT", "4")),
        max_examples=int(os.environ.get("MAX_EXAMPLES", "200")),
    )
    out = os.environ.get("OUTPUT_PATH", f"/tmp/shard_{cfg.shard_index}_result.json")
    result = run(cfg, out)
    print(json.dumps({k: v for k, v in result.items() if k != "sample"}, indent=2))
    print(f"\nWrote full result (incl. sample previews) to {out}")
