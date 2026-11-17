from datetime import datetime, timezone

from kubernetes import client as k

from cluster_poller import UNSCHEDULED_ID, build_graph, format_snapshot

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_node(name, cpu="4", memory="8Gi", control_plane=False, ready=True):
    labels = {"node-role.kubernetes.io/control-plane": ""} if control_plane else {}
    return k.V1Node(
        metadata=k.V1ObjectMeta(name=name, labels=labels),
        status=k.V1NodeStatus(
            allocatable={"cpu": cpu, "memory": memory},
            conditions=[k.V1NodeCondition(type="Ready", status="True" if ready else "False")],
        ),
    )


def make_pod(name, node=None, cpu="500m", memory="1Gi", phase="Running", namespace="default", job=None, waiting=None):
    owners = [k.V1OwnerReference(api_version="batch/v1", kind="Job", name=job, uid="u")] if job else None
    statuses = None
    if waiting:
        statuses = [
            k.V1ContainerStatus(
                name="c",
                image="img",
                image_id="",
                ready=False,
                restart_count=0,
                state=k.V1ContainerState(waiting=k.V1ContainerStateWaiting(reason=waiting)),
            )
        ]
    return k.V1Pod(
        metadata=k.V1ObjectMeta(name=name, namespace=namespace, owner_references=owners),
        spec=k.V1PodSpec(
            node_name=node,
            containers=[k.V1Container(name="c", resources=k.V1ResourceRequirements(requests={"cpu": cpu, "memory": memory}))],
        ),
        status=k.V1PodStatus(phase=phase, container_statuses=statuses),
    )


def make_job(name, namespace="default", parallelism=4, active=2, succeeded=1):
    return k.V1Job(
        metadata=k.V1ObjectMeta(name=name, namespace=namespace),
        spec=k.V1JobSpec(parallelism=parallelism, completions=parallelism, template=k.V1PodTemplateSpec()),
        status=k.V1JobStatus(active=active, succeeded=succeeded),
    )


def by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def test_node_utilization_is_requests_over_allocatable():
    graph = build_graph(
        [make_node("w1", cpu="4", memory="8Gi")],
        [make_pod("a", "w1", cpu="1", memory="2Gi"), make_pod("b", "w1", cpu="1", memory="2Gi")],
        [],
        now=NOW,
    )
    node = by_id(graph)["node/w1"]
    assert node["cpu"]["pct"] == 50.0
    assert node["memory"]["pct"] == 50.0
    assert node["utilization"] == 50.0
    assert node["pod_count"] == 2


def test_utilization_is_the_max_of_cpu_and_memory():
    graph = build_graph(
        [make_node("w1", cpu="4", memory="8Gi")],
        [make_pod("a", "w1", cpu="1", memory="6Gi")],
        [],
        now=NOW,
    )
    node = by_id(graph)["node/w1"]
    assert node["cpu"]["pct"] == 25.0
    assert node["memory"]["pct"] == 75.0
    assert node["utilization"] == 75.0


def test_finished_pods_do_not_hold_capacity():
    graph = build_graph(
        [make_node("w1", cpu="4")],
        [make_pod("done", "w1", cpu="2", phase="Succeeded"), make_pod("dead", "w1", cpu="2", phase="Failed")],
        [],
        now=NOW,
    )
    node = by_id(graph)["node/w1"]
    assert node["cpu"]["requested"] == 0
    assert node["pod_count"] == 0


def test_pending_pod_without_a_node_goes_to_unscheduled_bucket():
    graph = build_graph(
        [make_node("w1")],
        [make_pod("stuck", node=None, phase="Pending")],
        [],
        now=NOW,
    )
    nodes = by_id(graph)
    assert nodes["pod/default/stuck"]["parent"] == UNSCHEDULED_ID
    assert nodes[UNSCHEDULED_ID]["role"] == "unscheduled"
    assert nodes[UNSCHEDULED_ID]["pod_count"] == 1
    assert graph["summary"] == {"Pending": 1}


def test_no_unscheduled_bucket_when_everything_is_placed():
    graph = build_graph([make_node("w1")], [make_pod("a", "w1")], [], now=NOW)
    assert UNSCHEDULED_ID not in by_id(graph)


def test_pod_nests_under_its_node():
    graph = build_graph([make_node("w1"), make_node("w2")], [make_pod("a", "w2")], [], now=NOW)
    assert by_id(graph)["pod/default/a"]["parent"] == "node/w2"


def test_system_namespaces_hidden_but_still_count_toward_utilization():
    graph = build_graph(
        [make_node("w1", cpu="4")],
        [make_pod("coredns", "w1", cpu="1", namespace="kube-system"), make_pod("app", "w1", cpu="1")],
        [],
        now=NOW,
    )
    nodes = by_id(graph)
    assert "pod/kube-system/coredns" not in nodes
    assert "pod/default/app" in nodes
    assert nodes["node/w1"]["cpu"]["pct"] == 50.0


def test_explicit_namespaces_select_exactly_those():
    pods = [make_pod("coredns", "w1", namespace="kube-system"), make_pod("app", "w1")]
    graph = build_graph([make_node("w1")], pods, [], namespaces=["kube-system"], now=NOW)
    nodes = by_id(graph)
    assert "pod/kube-system/coredns" in nodes
    assert "pod/default/app" not in nodes


def test_job_owner_edges():
    graph = build_graph(
        [make_node("w1")],
        [make_pod("shard-0", "w1", job="infer"), make_pod("shard-1", "w1", job="infer"), make_pod("loner", "w1")],
        [make_job("infer", parallelism=4, active=2, succeeded=1)],
        now=NOW,
    )
    job = by_id(graph)["job/default/infer"]
    assert (job["parallelism"], job["active"], job["succeeded"]) == (4, 2, 1)
    assert sorted(e["target"] for e in graph["edges"]) == ["pod/default/shard-0", "pod/default/shard-1"]
    assert all(e["source"] == "job/default/infer" for e in graph["edges"])


def test_no_edge_when_owning_job_is_not_shown():
    graph = build_graph([make_node("w1")], [make_pod("orphan", "w1", job="gone")], [], now=NOW)
    assert graph["edges"] == []


def test_control_plane_sorts_first_and_is_labelled():
    graph = build_graph([make_node("w1"), make_node("cp", control_plane=True)], [], [], now=NOW)
    nodes = [n for n in graph["nodes"] if n["type"] == "node"]
    assert [n["label"] for n in nodes] == ["cp", "w1"]
    assert nodes[0]["role"] == "control-plane"
    assert nodes[1]["role"] == "worker"


def test_not_ready_node_is_flagged():
    graph = build_graph([make_node("w1", ready=False)], [], [], now=NOW)
    assert by_id(graph)["node/w1"]["ready"] is False


def test_pod_reason_surfaces_waiting_state():
    graph = build_graph([make_node("w1")], [make_pod("a", "w1", phase="Pending", waiting="ImagePullBackOff")], [], now=NOW)
    assert by_id(graph)["pod/default/a"]["reason"] == "ImagePullBackOff"


def test_snapshot_is_json_serialisable_and_renders():
    import json

    graph = build_graph(
        [make_node("w1")],
        [make_pod("a", "w1", job="infer"), make_pod("stuck", None, phase="Pending")],
        [make_job("infer")],
        cluster="test",
        now=NOW,
    )
    json.dumps(graph)
    text = format_snapshot(graph)
    assert "cluster: test" in text
    assert "default/a" in text
    assert "<unscheduled>" in text
    assert "job default/infer" in text
