# Day 1 — Milestones 1 & 2

## What's done

**Milestone 2 (worker ML logic, no k8s yet)** — built and smoke-tested:

- `inference/shard_loader.py` — deterministic dataset sharding (`ShardConfig` +
  `load_shard`). Validated with a synthetic 97-example dataset split into 1
  and 4 shards: every example accounted for exactly once, no drops or
  duplicates, and out-of-range `shard_index` raises cleanly.
  (Couldn't hit the real HF Hub from this sandbox — no network egress to
  `huggingface.co` here — so the sharding *math* is proven, but you'll want
  to re-run the real thing once on your machine, see below.)
- `inference/infer.py` — loads `sentence-transformers/all-MiniLM-L6-v2` on CPU,
  embeds a shard, writes per-shard JSON with timing + throughput
  (`examples_per_second`) and a few sample embedding previews.
- `inference/requirements.txt`, `inference/Dockerfile` — CPU-only image (no CUDA
  base, no `device="cuda"` anywhere). Model is pre-downloaded at build time
  so N pods don't all cold-start-download from the Hub simultaneously.

**Milestone 1 (local cluster)** — scaffolded, not yet run (needs Docker on
your machine, which this sandbox doesn't have):

- `infra/kind-cluster.yaml` — 4-node cluster (1 control-plane + 3 workers).

## Run this on your machine next

```bash
# 1. Sanity-check the ML path for real (needs your network access to HF Hub)
cd inference
pip install -r requirements.txt
DATASET_NAME=ag_news SHARD_INDEX=0 SHARD_COUNT=4 MAX_EXAMPLES=200 python3 infer.py

# 2. Bring up the local cluster
kind create cluster --name cluster-inference-radar --config ../infra/kind-cluster.yaml
kubectl get nodes -o wide
# You should see 1 control-plane + 3 worker nodes, all Ready.

# 3. Build the inference image and load it into kind
#    (kind clusters can't pull from a local Docker daemon directly — images
#    have to be explicitly loaded into the cluster's node containers)
docker build -t cluster-inference-worker:dev ./inference
kind load docker-image cluster-inference-worker:dev --name cluster-inference-radar

# 4. Smoke-test the image standalone (still no k8s Job yet, just confirming
#    the container itself runs the same way the bare script did)
docker run --rm \
  -e DATASET_NAME=ag_news -e SHARD_INDEX=0 -e SHARD_COUNT=4 -e MAX_EXAMPLES=200 \
  cluster-inference-worker:dev
```

If step 1 and step 4 both produce a result JSON with a real
`examples_per_second`, Milestone 2 is fully validated end to end and
Milestone 1's cluster is up — that's Day 1 done.

## Next up (Day 2 → Milestone 3)

The job dispatcher: use the `kubernetes` Python client to create a k8s `Job`
with `parallelism: N`, one pod per shard, `SHARD_INDEX` injected per-pod.
This is `controller/job_dispatcher.py` — not started yet.
