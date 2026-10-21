"""Observe POST behaviour with a synthetic local provider, without real secrets."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
import requests

import http_client


@pytest.mark.parametrize("result", [429, 503, "disconnect_after_body"])
def test_post_received_by_provider_is_not_replayed_on_ambiguous_failure(monkeypatch, result):
    received = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            if result == "disconnect_after_body":
                self.close_connection = True
                return
            self.send_response(result)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    # Reuse production retry policy against a loopback-only test provider.
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", requests.adapters.HTTPAdapter(max_retries=http_client._adapter.max_retries))
    monkeypatch.setattr(http_client, "_session", session)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/messages"
        if result == "disconnect_after_body":
            with pytest.raises(requests.exceptions.ConnectionError):
                http_client.post(url, json={"text": "synthetic response"}, timeout=(1, 1))
        else:
            assert http_client.post(url, json={"text": "synthetic response"}, timeout=(1, 1)).status_code == result
        assert len(received) == 1
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
