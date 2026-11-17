from fastapi.testclient import TestClient

from main import GraphCache, create_app


class StubPoller:
    def __init__(self, graph=None, error=None):
        self.graph, self.error = graph, error

    def snapshot(self):
        if self.error:
            raise self.error
        return self.graph


def client_for(poller, refresh=True):
    cache = GraphCache(poller)
    if refresh:
        cache.refresh()
    return TestClient(create_app(cache=cache, interval=3600))


def test_graph_endpoint_returns_cached_snapshot():
    graph = {"cluster": "c", "nodes": [], "edges": [], "summary": {}}
    response = client_for(StubPoller(graph)).get("/api/graph")
    assert response.status_code == 200
    assert response.json() == graph


def test_graph_endpoint_503_before_first_snapshot():
    response = client_for(StubPoller({}), refresh=False).get("/api/graph")
    assert response.status_code == 503
    assert "first" in response.json()["detail"]


def test_graph_endpoint_503_with_error_detail_when_cluster_unreachable():
    response = client_for(StubPoller(error=ConnectionError("connection refused"))).get("/api/graph")
    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"]


def test_cache_recovers_after_an_error():
    poller = StubPoller(error=ConnectionError("down"))
    cache = GraphCache(poller)
    cache.refresh()
    assert cache.error
    poller.error, poller.graph = None, {"nodes": []}
    cache.refresh()
    assert cache.error is None
    assert cache.graph == {"nodes": []}


def test_healthz_reports_connection_state():
    assert client_for(StubPoller({"nodes": []})).get("/healthz").json()["connected"] is True
    assert client_for(StubPoller(error=OSError("x"))).get("/healthz").json()["connected"] is False
