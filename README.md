# Cluster Inference & Compute Radar (Kubernetes)

**Phase 1**: distribute real inference workloads (from an HF dataset) across a Kubernetes
cluster. **Phase 2**: build a live graph visualization of cluster compute availability and job
flow on top of it — the graph *is* the cluster, not a mockup of one.

This is deliberately two phases so Phase 2 has a genuine workload to visualize instead of
synthetic/fake load — the radar is only convincing if it's showing something real happening.

## Why this project

This is a different problem than an eval-tracking platform: it's about *where compute goes and
how it's utilized*, not about logging experiment results. It also directly builds the k8s skills
(Jobs, scheduling, resource requests, RBAC) that show up in ML infra roles, and gives you a
legitimate "I built a distributed system and visualized it" story with real throughput numbers.

## Phase 1 — Cluster-Aware Batch Inference

### Architecture

```
[HF Dataset] --> [Job Dispatcher] --> splits into N shards
                        |
                        v
              [k8s Job: N worker pods] --> each pod loads its shard, runs inference
                        |
                        v
                 [Aggregator] --> writes results + throughput stats to Postgres
```

### What to build

- **Worker image**: loads a dataset shard (by index, passed via env var/ConfigMap) using HF
  `datasets`, runs a lightweight model (sentence-transformers embeddings or a small zero-shot
  classifier — pick something CPU-friendly so you don't need real GPUs to start), writes results.
- **Job dispatcher**: uses the `kubernetes` Python client to create a k8s `Job` with `parallelism:
  N`, one pod per shard. This is where you learn real scheduling behavior — what happens when you
  ask for more pods than the cluster has capacity for.
- **Aggregator**: watches for job completion (via the k8s API, not polling logs), pulls results,
  computes and stores throughput (examples/sec, total wall time vs. single-node baseline).
- **RBAC**: give the dispatcher a scoped service account with only the permissions it needs
  (create/watch Jobs, read Pods) — nice, genuine tie-in to the least-privilege work you already do
  at Schemata.

### Milestones

1. Local cluster via `kind` or `k3d` (no cloud costs to start). Get comfortable with `kubectl`.
2. Containerize a worker that loads a fixed shard and runs inference locally (before touching
   k8s at all — get the ML part working first).
3. Job dispatcher: split the dataset, launch a k8s `Job` with N parallel pods, each reading its
   shard index from an env var.
4. Aggregator: collect and store results + timing.
5. Add resource `requests`/`limits` per pod; deliberately under-provision the cluster once and
   observe pending pods / scheduling behavior — this is a good "what I learned" story.
6. Least-privilege RBAC service account for the dispatcher.
7. Write-up: throughput at N=1 vs N=4 vs N=8 workers, and what broke along the way.

## Phase 2 — Compute Availability Radar

### Architecture

```
[k8s API] <--poll-- [Cluster Poller] --> [FastAPI backend] --> [Graph frontend]
                                                                 (nodes = k8s nodes,
                                                                  children = live pods,
                                                                  edges = job DAG,
                                                                  color = utilization)
```

### What to build

- **Cluster poller**: uses the `kubernetes` Python client to pull, every few seconds, node
  capacity vs. allocated resources, and current pod states.
- **Backend API** (FastAPI): exposes current graph state as JSON — nodes (cluster nodes + their
  utilization %), children (active pods, which node they're on), and edges (the shard → infer →
  aggregate DAG from Phase 1).
- **Frontend graph**: React + Cytoscape.js (or React Flow) rendering the above — node color
  intensity for utilization, pods appearing/disappearing in real time as Phase 1 jobs run,
  animated edges for active data flow.
- **Live demo**: kick off a Phase 1 batch job and watch the radar show pods getting scheduled,
  utilization climbing, and the job DAG completing — this is the actual demo-able moment.

### Milestones

8. Cluster poller running standalone, printing node/pod state to console.
9. FastAPI backend serving that state as JSON (poll or websocket).
10. Static graph render in the frontend from a JSON snapshot (get the visualization right before
    making it live).
11. Wire the frontend to live-poll the backend; run a Phase 1 job and watch it animate.
12. Stretch: highlight pending/unschedulable pods differently when the cluster is at capacity —
    sets up a natural "v2: build a custom scheduler" story if you want to keep growing this later.

## Repo structure

```
cluster-inference-radar/
├── README.md
├── infra/
│   ├── kind-cluster.yaml
│   └── k8s/
│       ├── worker-job-template.yaml
│       ├── aggregator-deployment.yaml
│       └── rbac.yaml
├── worker/
│   ├── shard_loader.py
│   ├── infer.py
│   └── Dockerfile
├── controller/
│   ├── job_dispatcher.py
│   └── aggregator.py
├── radar/
│   ├── backend/
│   │   ├── cluster_poller.py
│   │   └── main.py            # FastAPI app
│   └── frontend/
│       └── graph_view/        # React + Cytoscape.js
├── datasets/
│   └── loaders.py
└── tests/
```

## Tech stack

- `kind` or `k3d` for a free local cluster
- `kubernetes` Python client for both the dispatcher and the poller
- HF `datasets` + a lightweight model (sentence-transformers, or a small HF classification
  pipeline) as the real workload
- Postgres for results/throughput storage
- FastAPI for the radar backend
- React + Cytoscape.js (or React Flow) for the graph frontend
- Optional stretch: Helm chart for cleaner deploys; move from `kind` to a real cloud cluster (EKS)
  once Phase 1+2 work locally

## Why this sequencing works

Building Phase 1 first means Phase 2's graph is showing genuine cluster behavior — real pods
being scheduled, real utilization changing — rather than a mocked-up animation. That's the
difference between "a nice D3 demo" and "a tool that shows you what your cluster is actually
doing," which is a much stronger thing to have built.
