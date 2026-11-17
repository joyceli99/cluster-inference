"""Read-only cluster poller: turns Kubernetes node/pod/Job state into a graph snapshot.

Snapshot shape (JSON-serialisable):

    nodes  cluster nodes (type "node"), pods (type "pod", `parent` = hosting node id)
           and Jobs (type "job")
    edges  Job -> Pod ownership
    summary  pod counts by status

Utilisation is requested / allocatable, not live usage -- kind has no metrics-server,
and requests are what the scheduler actually bases placement on.

Run standalone to print live cluster state:  python cluster_poller.py [--interval 3]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.utils import parse_quantity

SYSTEM_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease", "local-path-storage"})
UNSCHEDULED_ID = "node/<unscheduled>"
TERMINAL_PHASES = frozenset({"Succeeded", "Failed"})
CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")
REQUEST_TIMEOUT_S = 10


def _quantity(value) -> float:
    return float(parse_quantity(value)) if value else 0.0


def _pct(used: float, total: float) -> float:
    return round(100.0 * used / total, 1) if total else 0.0


def pod_requests(pod) -> tuple[float, float]:
    """Summed (cpu cores, memory bytes) requested by a pod's containers."""
    cpu = mem = 0.0
    for container in pod.spec.containers or []:
        requests = (container.resources.requests if container.resources else None) or {}
        cpu += _quantity(requests.get("cpu"))
        mem += _quantity(requests.get("memory"))
    return cpu, mem


def _node_ready(node) -> bool:
    return any(c.type == "Ready" and c.status == "True" for c in node.status.conditions or [])


def _node_role(node) -> str:
    labels = node.metadata.labels or {}
    return "control-plane" if any(label in labels for label in CONTROL_PLANE_LABELS) else "worker"


def _pod_status(pod) -> str:
    if pod.metadata.deletion_timestamp:
        return "Terminating"
    return pod.status.phase or "Unknown"


def _pod_reason(pod) -> str | None:
    """Why a pod isn't progressing (ImagePullBackOff, Unschedulable, ...), if known."""
    for cs in pod.status.container_statuses or []:
        waiting = cs.state.waiting if cs.state else None
        if waiting and waiting.reason:
            return waiting.reason
    for cond in pod.status.conditions or []:
        if cond.type == "PodScheduled" and cond.status == "False":
            return cond.reason
    return None


def build_graph(nodes, pods, jobs, *, cluster: str = "", namespaces=None, now=None) -> dict:
    """Build a graph snapshot from Kubernetes API objects.

    Node utilisation always counts every pod on the node (system pods consume real
    capacity); only pods in the shown namespaces appear in the graph. `namespaces`
    selects exactly those namespaces, or None for everything except system ones.
    """

    def shown(namespace: str) -> bool:
        return namespace in namespaces if namespaces else namespace not in SYSTEM_NAMESPACES

    load = {n.metadata.name: {"cpu": 0.0, "memory": 0.0, "pods": 0} for n in nodes}
    for pod in pods:
        host = pod.spec.node_name
        if host in load and pod.status.phase not in TERMINAL_PHASES:
            cpu, mem = pod_requests(pod)
            load[host]["cpu"] += cpu
            load[host]["memory"] += mem
            load[host]["pods"] += 1

    graph_nodes = []
    for node in sorted(nodes, key=lambda n: (_node_role(n) != "control-plane", n.metadata.name)):
        name = node.metadata.name
        allocatable = node.status.allocatable or {}
        cpu_alloc = _quantity(allocatable.get("cpu"))
        mem_alloc = _quantity(allocatable.get("memory"))
        cpu_pct = _pct(load[name]["cpu"], cpu_alloc)
        mem_pct = _pct(load[name]["memory"], mem_alloc)
        graph_nodes.append(
            {
                "id": f"node/{name}",
                "type": "node",
                "label": name,
                "role": _node_role(node),
                "ready": _node_ready(node),
                "cpu": {"allocatable": cpu_alloc, "requested": round(load[name]["cpu"], 3), "pct": cpu_pct},
                "memory": {"allocatable": mem_alloc, "requested": load[name]["memory"], "pct": mem_pct},
                "utilization": max(cpu_pct, mem_pct),
                "pod_count": load[name]["pods"],
            }
        )

    known_nodes = {n["id"] for n in graph_nodes}
    job_ids = {}
    for job in sorted(jobs, key=lambda j: (j.metadata.namespace, j.metadata.name)):
        if not shown(job.metadata.namespace):
            continue
        job_id = f"job/{job.metadata.namespace}/{job.metadata.name}"
        job_ids[(job.metadata.namespace, job.metadata.name)] = job_id
        status = job.status
        graph_nodes.append(
            {
                "id": job_id,
                "type": "job",
                "label": job.metadata.name,
                "namespace": job.metadata.namespace,
                "parallelism": job.spec.parallelism,
                "completions": job.spec.completions,
                "active": (status.active if status else 0) or 0,
                "succeeded": (status.succeeded if status else 0) or 0,
                "failed": (status.failed if status else 0) or 0,
            }
        )

    edges = []
    summary: dict[str, int] = {}
    unscheduled = 0
    for pod in sorted(pods, key=lambda p: (p.metadata.namespace, p.metadata.name)):
        namespace = pod.metadata.namespace
        if not shown(namespace):
            continue
        host_id = f"node/{pod.spec.node_name}" if pod.spec.node_name else None
        parent = host_id if host_id in known_nodes else UNSCHEDULED_ID
        unscheduled += parent == UNSCHEDULED_ID
        cpu, mem = pod_requests(pod)
        status = _pod_status(pod)
        summary[status] = summary.get(status, 0) + 1
        pod_id = f"pod/{namespace}/{pod.metadata.name}"
        graph_nodes.append(
            {
                "id": pod_id,
                "type": "pod",
                "label": pod.metadata.name,
                "namespace": namespace,
                "status": status,
                "reason": _pod_reason(pod),
                "parent": parent,
                "cpu_request": cpu,
                "memory_request": mem,
            }
        )
        owner = next((o for o in pod.metadata.owner_references or [] if o.kind == "Job"), None)
        job_id = job_ids.get((namespace, owner.name)) if owner else None
        if job_id:
            edges.append({"id": f"{job_id}->{pod_id}", "source": job_id, "target": pod_id, "type": "owner"})

    if unscheduled:
        graph_nodes.append(
            {
                "id": UNSCHEDULED_ID,
                "type": "node",
                "label": "unscheduled",
                "role": "unscheduled",
                "ready": False,
                "utilization": 0.0,
                "pod_count": unscheduled,
            }
        )

    return {
        "cluster": cluster,
        "timestamp": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "nodes": graph_nodes,
        "edges": edges,
        "summary": summary,
    }


class ClusterPoller:
    """Lists nodes, pods and Jobs through the Kubernetes API (read-only)."""

    def __init__(self, context: str | None = None, namespaces: list[str] | None = None):
        self._context = context
        self._namespaces = namespaces or None
        self._core = None
        self._batch = None
        self.cluster = ""

    def _connect(self) -> None:
        try:
            api_client = config.new_client_from_config(context=self._context)
            _, active = config.list_kube_config_contexts()
            cluster = self._context or active["name"]
        except config.ConfigException:
            config.load_incluster_config()
            api_client, cluster = client.ApiClient(), "in-cluster"
        self._core, self._batch, self.cluster = client.CoreV1Api(api_client), client.BatchV1Api(api_client), cluster

    def snapshot(self) -> dict:
        if self._core is None:
            self._connect()
        nodes = self._core.list_node(_request_timeout=REQUEST_TIMEOUT_S).items
        pods = self._core.list_pod_for_all_namespaces(_request_timeout=REQUEST_TIMEOUT_S).items
        jobs = self._batch.list_job_for_all_namespaces(_request_timeout=REQUEST_TIMEOUT_S).items
        return build_graph(nodes, pods, jobs, cluster=self.cluster, namespaces=self._namespaces)


def namespaces_from_env() -> list[str] | None:
    raw = os.environ.get("RADAR_NAMESPACES", "")
    return [ns.strip() for ns in raw.split(",") if ns.strip()] or None


def _fmt_bytes(value: float) -> str:
    return f"{value / 2**30:.1f}Gi" if value >= 2**30 else f"{value / 2**20:.0f}Mi"


def format_snapshot(graph: dict) -> str:
    """Human-readable console rendering of a snapshot."""
    summary = ", ".join(f"{count} {status.lower()}" for status, count in sorted(graph["summary"].items()))
    lines = [f"cluster: {graph['cluster']}   {graph['timestamp']}   pods: {summary or 'none'}", ""]

    pods_by_parent: dict[str, list[dict]] = {}
    for item in graph["nodes"]:
        if item["type"] == "pod":
            pods_by_parent.setdefault(item["parent"], []).append(item)

    rows = [("NODE", "ROLE", "CPU req/alloc", "MEM req/alloc", "PODS", "UTIL")]
    node_ids = []
    for node in (n for n in graph["nodes"] if n["type"] == "node"):
        node_ids.append(node["id"])
        if node["role"] == "unscheduled":
            rows.append(("<unscheduled>", "-", "-", "-", str(node["pod_count"]), "-"))
            continue
        cpu, mem = node["cpu"], node["memory"]
        rows.append(
            (
                node["label"],
                node["role"],
                f"{cpu['requested']:.2f}/{cpu['allocatable']:.2f} ({cpu['pct']:.0f}%)",
                f"{_fmt_bytes(mem['requested'])}/{_fmt_bytes(mem['allocatable'])} ({mem['pct']:.0f}%)",
                str(node["pod_count"]),
                f"{node['utilization']:.0f}%",
            )
        )

    widths = [max(len(row[i]) for row in rows) for i in range(6)]

    def render(row) -> str:
        cells = [cell.ljust(width) for cell, width in zip(row[:4], widths)]
        cells += [cell.rjust(width) for cell, width in zip(row[4:], widths[4:])]
        return "  ".join(cells)

    lines.append(render(rows[0]))
    for node_id, row in zip(node_ids, rows[1:]):
        lines.append(render(row))
        for pod in pods_by_parent.get(node_id, []):
            reason = f" ({pod['reason']})" if pod["reason"] else ""
            lines.append(f"    - {pod['namespace']}/{pod['label']}  {pod['status']}{reason}")

    for job in (n for n in graph["nodes"] if n["type"] == "job"):
        lines.append(
            f"job {job['namespace']}/{job['label']}: active={job['active']} "
            f"succeeded={job['succeeded']}/{job['completions']} failed={job['failed']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print live Kubernetes node/pod state.")
    parser.add_argument("--context", default=os.environ.get("RADAR_KUBE_CONTEXT"), help="kubeconfig context")
    parser.add_argument("--namespace", action="append", help="show only this namespace (repeatable)")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    parser.add_argument("--json", action="store_true", help="print the raw graph JSON")
    args = parser.parse_args()

    poller = ClusterPoller(context=args.context, namespaces=args.namespace or namespaces_from_env())
    while True:
        try:
            graph = poller.snapshot()
        except Exception as exc:
            print(f"poll failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            if args.once:
                return 1
        else:
            if not args.once and sys.stdout.isatty():
                print("\033[H\033[J", end="")
            print(json.dumps(graph, indent=2) if args.json else format_snapshot(graph), flush=True)
            if args.once:
                return 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
