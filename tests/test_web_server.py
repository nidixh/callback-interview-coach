# test_web_server.py
"""The local server: every page and every read-only API answers, and nothing else.

The server is started on a free port with a stand-in for the interview service, since none of these routes run an interview.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

import web_server


class NoInterview:
    """Stands in for the live interview service; these routes never use it."""


@pytest.fixture
def base(data_dir):
    server = web_server.make_server(service=NoInterview(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


@pytest.mark.parametrize("path, kind", [
    ("/", "text/html"), ("/practise", "text/html"), ("/favicon.svg", "image/svg+xml"),
    ("/img/how/setup.webp", "image/webp"),
])
def test_the_pages_and_their_files_are_served(base, path, kind):
    status, content_type, body = get(base + path)
    assert status == 200 and content_type.startswith(kind) and body


@pytest.mark.parametrize("path", ["/api/showcase", "/api/showcase/strong", "/api/example", "/api/calendar",
                                  "/api/cv", "/api/sessions", "/api/history"])
def test_the_read_only_apis_answer_with_json(base, path):
    status, content_type, body = get(base + path)
    assert status == 200 and content_type.startswith("application/json")
    json.loads(body)


def test_an_example_is_dealt_at_the_level_asked_for(base):
    _, _, body = get(base + "/api/example?level=easy")
    job = json.loads(body)
    assert job["level"] == "easy" and job["question_count"] == 3 and job["job_description"]


def test_an_advert_is_measured(base):
    req = urllib.request.Request(base + "/api/difficulty", data=json.dumps({"text": "Wash pots."}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert json.loads(r.read())["level"] == "easy"


@pytest.mark.parametrize("path", ["/nope", "/../README.md", "/api/showcase/..%2f..%2fREADME"])
def test_anything_else_is_refused(base, path):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + path)
    assert e.value.code == 404
