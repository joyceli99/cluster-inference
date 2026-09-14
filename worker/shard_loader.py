"""
Deterministic dataset sharding.

Given a HuggingFace dataset name/split and a total shard count, returns the
slice of examples that belong to a specific shard index. This is the same
logic whether it's called locally (Milestone 2) or inside a k8s pod
(Milestone 3+), where SHARD_INDEX / SHARD_COUNT come from env vars.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from datasets import Dataset, load_dataset


@dataclass
class ShardConfig:
    dataset_name: str
    split: str
    text_column: str
    shard_index: int
    shard_count: int
    max_examples: int | None = None  # cap total examples before sharding (dev/testing)

    @classmethod
    def from_env(cls) -> "ShardConfig":
        """Build config from environment variables (how a k8s pod will get it)."""
        max_examples_env = os.environ.get("MAX_EXAMPLES")
        return cls(
            dataset_name=os.environ["DATASET_NAME"],
            split=os.environ.get("DATASET_SPLIT", "train"),
            text_column=os.environ.get("TEXT_COLUMN", "text"),
            shard_index=int(os.environ["SHARD_INDEX"]),
            shard_count=int(os.environ["SHARD_COUNT"]),
            max_examples=int(max_examples_env) if max_examples_env else None,
        )


def load_shard(config: ShardConfig) -> Dataset:
    """Load only this shard's slice of the dataset.

    Uses HF `datasets`' own `.shard()` so we don't pull the full dataset into
    memory on every worker just to throw most of it away -- important once
    this runs as N parallel pods instead of one local process.
    """
    if not (0 <= config.shard_index < config.shard_count):
        raise ValueError(
            f"shard_index {config.shard_index} out of range for "
            f"shard_count {config.shard_count}"
        )

    ds = load_dataset(config.dataset_name, split=config.split)

    if config.max_examples is not None:
        ds = ds.select(range(min(config.max_examples, len(ds))))

    if config.text_column not in ds.column_names:
        raise ValueError(
            f"text_column '{config.text_column}' not found in dataset columns: "
            f"{ds.column_names}"
        )

    shard = ds.shard(num_shards=config.shard_count, index=config.shard_index, contiguous=True)
    return shard


if __name__ == "__main__":
    # Quick manual check: `python shard_loader.py` with env vars set,
    # or edit the defaults below for an ad-hoc local run.
    cfg = ShardConfig(
        dataset_name=os.environ.get("DATASET_NAME", "ag_news"),
        split=os.environ.get("DATASET_SPLIT", "train"),
        text_column=os.environ.get("TEXT_COLUMN", "text"),
        shard_index=int(os.environ.get("SHARD_INDEX", "0")),
        shard_count=int(os.environ.get("SHARD_COUNT", "4")),
        max_examples=int(os.environ.get("MAX_EXAMPLES", "200")),
    )
    shard = load_shard(cfg)
    print(f"Loaded shard {cfg.shard_index}/{cfg.shard_count}: {len(shard)} examples")
    print(shard[0])
