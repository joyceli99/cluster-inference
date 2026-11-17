# Cluster Inference & Compute Radar

A distributed inference system for Kubernetes: a dispatcher shards a real HuggingFace dataset
across a cluster for parallel CPU inference, and a live graph visualization shows cluster compute
state and job flow as work executes. Runs entirely on a local `kind` cluster — no cloud account
or GPU required.

## Overview

The project has two parts:

- **Cluster-aware batch inference** — a dispatcher splits a dataset into shards and runs them in
  parallel as pods in a Kubernetes `Job`; an aggregator collects results and throughput stats.
- **Compute availability radar** — a live node graph of the cluster (nodes, pods, utilization,
  job flow), built on the same Kubernetes API, updating in real time as jobs run.

## Architecture

```
[HF Dataset] --> [Job Dispatcher] --> N shards
                       |
                       v
         [k8s Job: N worker pods] --> inference per shard
                       |
                       v
                [Aggregator] --> Postgres (results + throughput)

[k8s API] --> [Cluster Poller] --> [FastAPI backend] --> [React graph frontend]
```

## Repo layout

```
cluster-inference/
├── inference/       # containerized inference worker (shard loading + CPU inference)
├── infra/           # kind cluster config, k8s manifests
├── controller/      # job dispatcher, aggregator (planned)
├── radar/           # cluster poller, FastAPI backend, React graph frontend
├── tests/           # unit tests for the radar backend
├── SPEC.md          # full design spec, milestones, and roadmap
└── STATUS.md        # build status
```

## Tech stack

- Kubernetes (`kind`) for local cluster orchestration
- `kubernetes` Python client for the dispatcher and cluster poller
- HuggingFace `datasets` + `sentence-transformers` for the inference workload
- Postgres for results and throughput storage
- FastAPI + React/Cytoscape.js for the radar visualization

## Getting started

```bash
# Run the inference worker locally
cd inference
pip install -r requirements.txt
DATASET_NAME=ag_news SHARD_INDEX=0 SHARD_COUNT=4 python3 infer.py

# Bring up the local cluster
kind create cluster --name cluster-inference-radar --config infra/kind-cluster.yaml
kubectl get nodes
```

### Run the radar

Point `kubectl` at a cluster, then start the backend and the graph view:

```bash
python3 -m venv .venv && .venv/bin/pip install -r radar/backend/requirements-dev.txt
.venv/bin/python radar/backend/cluster_poller.py                       # console view of nodes/pods
.venv/bin/uvicorn main:app --app-dir radar/backend --port 8000         # graph API
(cd radar/frontend/graph_view && npm install && npm run dev)           # http://localhost:5173
.venv/bin/python -m pytest                                             # tests
```

The radar is read-only and uses your current kubeconfig context (override with
`RADAR_KUBE_CONTEXT`; limit namespaces with `RADAR_NAMESPACES=ns1,ns2`). Open
`http://localhost:5173/?snapshot` to view a captured sample without a cluster.

See [SPEC.md](SPEC.md) for the full design, phased approach, and definition of done, and
[STATUS.md](STATUS.md) for current build status.
