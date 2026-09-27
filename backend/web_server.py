# web_server.py
"""The local web server for the app.

Usage: python backend/web_server.py (from the project folder)

Serves the pages on 127.0.0.1 only, so recordings, transcripts and reports never leave the computer.
Uses only the Python standard library.

Actions are plain POST requests.
The interview state is streamed to the page with Server-Sent Events.
The camera preview is served as MJPEG for an img tag, because the recording worker already holds the camera and Windows lets only one program open it.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import queue
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote

import live_service

logger = logging.getLogger(__name__)

# The server lives in backend/; the pages, the example files and the environments sit beside that folder, at the top of the project.
HERE = Path(__file__).parent
ROOT = HERE.parent
WEB_ROOT = ROOT / "frontend"
FIXTURES = ROOT / "artifacts" / "samples"

# Windows does not know the web font types, so they are added here.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("image/webp", ".webp")
# The start screen's script.
# Some Windows registries map .js to text/plain, which a browser refuses to run.
mimetypes.add_type("text/javascript", ".js")

HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# How long the preview waits for a new frame once frames have started, before deciding the camera has stopped.
PREVIEW_IDLE_TIMEOUT_S = 5.0

# How long the preview waits for the first frame.
# Longer, because the camera takes time to open, and an img that gives up never retries.
PREVIEW_STARTUP_TIMEOUT_S = 45.0
PREVIEW_INTERVAL_S = 1 / 12.0

_BOUNDARY = "coachframe"


class _Handler(BaseHTTPRequestHandler):
    server_version = "InterviewCoach"

    # The default logs every request to stderr, which buries anything useful during an interview.
    def log_message(self, fmt, *args):  # name fixed by the base
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # Helpers

    @property
    def service(self) -> live_service.LiveService:
        return self.server.service

    def _send_json(self, payload, status: int = 200, no_store: bool = False) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # What the camera check and the live light may read, and nothing else the worker happens to write.
    _CAMERA_FIELDS = {"looking": (bool, type(None)), "framing": (str, type(None)),
                      "light": (str, type(None))}

    def _camera_status(self) -> dict:
        """Whether the camera can see a face and, with gaze, where the candidate is looking.

        `face` is null when nothing is publishing, and `looking` is null until gaze has seen something.
        """
        unknown = {"face": None, "looking": None, "framing": None, "light": None,
                   "calibrated": False, "gaze": False, "box": None}
        source = self.server.camera_status
        if source is None:
            return unknown
        try:
            status = source()
        except Exception:  # an indicator, not the interview
            return unknown
        if not isinstance(status, dict) or "face" not in status:
            return unknown
        out = dict(unknown, face=bool(status["face"]),
                   calibrated=bool(status.get("calibrated")), gaze=bool(status.get("gaze")))
        for name, kinds in self._CAMERA_FIELDS.items():
            if isinstance(status.get(name), kinds):
                out[name] = status.get(name)
        box = status.get("box")
        if (isinstance(box, list) and len(box) == 3
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0
                        for v in box)):
            out["box"] = [float(v) for v in box]
        return out

    def _serve_showcase(self, name: str) -> None:
        """One saved example session, summarised the same way as a live one.

        Loads no model, so it opens instantly.
        The name is matched against the known list, never joined onto a path.
        """
        import live_service

        available = {s["name"]: s for s in list_showcases()}
        chosen = available.get(name)
        if chosen is None:
            self._send_json({"error": "no such showcase"}, status=404)
            return
        try:
            session = json.loads(
                Path(chosen["path"]).read_text(encoding="utf-8"))
        except Exception as exc:
            # A bad file is not a crash.
            self._send_json({"error": f"could not be read: {exc}"}, status=500)
            return
        self._send_json({
            "name": name,
            "label": chosen["label"],
            "note": chosen["note"],
            "summary": live_service.summarise(session),
            "view": live_service.report_view(session),
            "warnings": list(session.get("warnings") or []),
        })

    def _serve_history(self) -> None:
        """Every past session, and what changed since the first.

        Catches its own errors so the request still gets an answer.
        """
        import session_history

        try:
            entries = session_history.load()
            self._send_json({"sessions": entries,
                             "deltas": session_history.deltas(entries)})
        except Exception as exc:
            # A bad file is not a crash.
            self._send_json({"error": f"could not be read: {exc}"}, status=500)

    def _answer_at(self, raw: str, session=None):
        """The nth answer of the current report, or None."""
        try:
            index = int(raw)
        except ValueError:
            return None
        answers = ((session if session is not None else getattr(self.service, "report", None)) or {}).get("answers") or []
        if not 1 <= index <= len(answers):
            return None
        return answers[index - 1] or {}

    def _serve_answer_detail(self, raw: str, session=None) -> None:
        """The words of one answer, with their timings.

        Sent only when an answer is opened.
        The recording's file path is left out.
        """
        answer = self._answer_at(raw, session)
        if answer is None:
            self._send_json({"error": "no such answer"}, status=404)
            return

        speech = answer.get("speech") or {}
        transcription = speech.get("transcription") or {}
        words = [
            {"word": w.get("word", ""), "start": w.get("start"),
             "end": w.get("end")}
            for w in (transcription.get("word_timestamps") or [])
            if isinstance(w, dict)
        ]
        fillers = [
            {"filler": f.get("filler", ""), "start": f.get("start"),
             "end": f.get("end")}
            for f in ((speech.get("fillers") or {}).get("instances") or [])
            if isinstance(f, dict)
        ]
        self._send_json({
            "index": answer.get("index"),
            "question": answer.get("question", ""),
            "transcript": transcription.get("text", ""),
            "words": words,
            "fillers": fillers,
            "duration": speech.get("audio_duration_seconds"),
        })

    def _serve_answer_audio(self, path: str, session=None) -> None:
        """Send one recorded answer so the candidate can hear it.

        The page names an answer by its number, and the path comes from the report, not the request.
        It is also checked to be a wav inside the recordings folder.
        """
        import session_store

        def refuse() -> None:
            self._send_json({"error": "no such recording"}, status=404)

        answer = self._answer_at(path[len("/api/answer/"):-len("/audio")], session)
        if answer is None:
            refuse()
            return

        named = answer.get("audio_file") or ""
        if not named:
            refuse()
            return

        try:
            target = Path(named).resolve()
            target.relative_to(session_store.root().resolve())
        except (ValueError, OSError):
            refuse()
            return

        if target.suffix.lower() != ".wav" or not target.is_file():
            refuse()
            return

        self._send_audio(target)

    def _send_audio(self, target: Path) -> None:
        """Send a wav, supporting byte ranges so the player can jump to a moment."""
        size = target.stat().st_size
        start, end = 0, size - 1

        spec = (self.headers.get("Range") or "").strip()
        wants_range = spec.startswith("bytes=")
        if wants_range:
            first, _, last = spec[len("bytes="):].partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    # "bytes=-500": the final 500 bytes.
                    start, end = max(0, size - int(last)), size - 1
                else:
                    wants_range = False
            except ValueError:
                wants_range = False

        end = min(end, size - 1)
        if wants_range and (start > end or start >= size):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        length = end - start + 1
        self.send_response(206 if wants_range else 200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if wants_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with open(target, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_example(self, level: str = "") -> None:
        """An advert and a CV to fill the form with.

        With a level (easy, medium or hard) the next example comes from artifacts/examples.
        Without one it is the default example from artifacts/samples.
        A missing file only loses the example.
        """
        def read(name: str) -> str:
            try:
                return (FIXTURES / name).read_text(encoding="utf-8").strip()
            except Exception:
                # No fixtures is not a failure.
                return ""

        if level:
            try:
                dealt = self.server.examples.deal(level)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            if dealt is None:
                self._send_json({"error": f"No {level} examples are installed."},
                                status=404)
                return
            self._send_json(dealt, no_store=True)
            return

        advert = read("jd_data_engineer_real.txt")
        self._send_json({
            "job_title": advert.splitlines()[0].strip() if advert else "",
            "job_description": advert,
            "cv": read("sample_cv.txt"),
        })

    def _send_calendar(self) -> None:
        import interview_calendar

        self._send_json({"interviews": interview_calendar.load(),
                         "next": interview_calendar.next_upcoming()}, no_store=True)

    def _serve_cv(self) -> None:
        """The stored CV, if the candidate has kept one here."""
        import candidate_cv

        try:
            text = candidate_cv.load()
            self._send_json({"text": text, "stored": bool(text)})
        except Exception as exc:
            # A bad file is not a crash.
            self._send_json({"error": f"could not be read: {exc}"}, status=500)

    def _serve_sessions(self) -> None:
        """Every past session that recorded something.

        Catches its own errors so the request still gets an answer.
        """
        import session_library

        try:
            self._send_json({"sessions": session_library.entries()})
        except Exception as exc:
            # A bad folder is not a crash.
            self._send_json({"error": f"could not be read: {exc}"}, status=500)

    def _serve_past_report(self, name: str) -> None:
        """The written report of an earlier session.

        The name is looked up by session_library, never joined onto a path.
        """
        import session_library

        try:
            # A page once sent the whole directory path rather than its name; the last component is the session either way.
            wanted = unquote(name).replace("\\", "/").rstrip("/").split("/")[-1]
            target = session_library.report_pdf(wanted)
        except Exception as exc:
            # A bad folder is not a crash.
            self._send_json({"error": f"could not be read: {exc}"}, status=500)
            return

        if target is None:
            self._send_json({"error": "no report for that session"}, status=404)
            return

        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        # Named for the session: eight reports all called interview_report.pdf are eight files nobody can tell apart in a downloads folder.
        self.send_header("Content-Disposition",
                         f'inline; filename="{target.parent.name}.pdf"')
        self.end_headers()
        self.wfile.write(body)

    def _serve_past(self, rest: str) -> None:
        """A past session, read back from its saved result.

        /api/sessions/<name>/report is the full report; .../answer/<i> and .../answer/<i>/audio work as they do for the current session.
        The name is looked up by session_library, never joined onto a path.
        """
        import live_service
        import session_library

        name, _, tail = rest.partition("/")
        session = session_library.result(unquote(name))
        if session is None:
            self._send_json({"error": "no report for that session"}, status=404)
            return
        if tail == "report":
            self._send_json(live_service.report_view(session))
        elif tail.startswith("answer/") and tail.endswith("/audio"):
            self._serve_answer_audio("/api/" + tail, session)
        elif tail.startswith("answer/"):
            self._serve_answer_detail(tail[len("answer/"):], session)
        else:
            self._send_json({"error": "not found"}, status=404)

    def _serve_report_pdf(self) -> None:
        """Send the written report, if there is one.

        If the server was restarted and no longer remembers it, the newest report on disk is used.
        """
        import session_library

        target = getattr(self.server.service, "report_pdf", "")
        if not target or not Path(target).is_file():
            target = ""
            try:
                for entry in session_library.entries():
                    if entry.get("has_report"):
                        found = session_library.report_pdf(entry["name"])
                        target = str(found) if found else ""
                        break
            except Exception as exc:
                # A bad folder is not a crash.
                logger.warning("Could not look for a report on disk: %s", exc)
        if not target:
            self._send_json({"error": "no report has been written"}, status=404)
            return

        body = Path(target).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         'inline; filename="interview_report.pdf"')
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            # A bad body is a bad request.
            return {}

    # Routes

    def do_GET(self) -> None:
        # Name fixed by the base class.
        """Route a GET request to the matching page, file or API handler."""
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            self._send_json(self.service.snapshot())
        elif path == "/api/camera":
            self._send_json(self._camera_status())
        elif path == "/api/level":
            # Polled while the candidate answers, so it is kept out of the event stream and never cached.
            self._send_json(self.service.level(), no_store=True)
        elif path == "/api/ready":
            self._send_json(readiness())
        elif path == "/api/showcase":
            self._send_json({"sessions": list_showcases()})
        elif path == "/api/cv":
            self._serve_cv()
        elif path == "/api/calendar":
            self._send_calendar()
        elif path == "/api/example":
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            self._serve_example((query.get("level") or [""])[0])
        elif path == "/api/sessions":
            self._serve_sessions()
        elif path.startswith("/api/sessions/") and path.endswith("/report.pdf"):
            self._serve_past_report(
                path[len("/api/sessions/"):-len("/report.pdf")])
        elif path.startswith("/api/sessions/"):
            self._serve_past(path[len("/api/sessions/"):])
        elif path == "/api/history":
            self._serve_history()
        elif path.startswith("/api/showcase/"):
            self._serve_showcase(path[len("/api/showcase/"):])
        elif path.startswith("/api/answer/") and path.endswith("/audio"):
            self._serve_answer_audio(path)
        elif path.startswith("/api/answer/"):
            self._serve_answer_detail(path[len("/api/answer/"):])
        elif path == "/api/report":
            import live_service
            view = live_service.report_view(self.service.report) or {}
            # The coach's notes progress, so a reloaded page knows the report is not finished.
            writing = (self.service.snapshot().get("summary") or {}).get("writing")
            if view.get("summary") is not None and writing:
                view["summary"]["writing"] = writing
            self._send_json(view)
        elif path == "/api/report.pdf":
            self._serve_report_pdf()
        elif path == "/api/events":
            self._stream_events()
        elif path == "/api/preview.mjpg":
            self._stream_preview()
        else:
            self._serve_file(path)

    def do_POST(self) -> None:
        # Name fixed by the base class.
        """Route a POST request to the matching API handler."""
        path = self.path.split("?", 1)[0]
        body = self._read_json()

        if path == "/api/start":
            self.service.start(
                job_description=str(body.get("job_description") or ""),
                job_title=str(body.get("job_title") or ""),
                question_count=int(body.get("question_count") or 6),
                use_video=bool(body.get("use_video", True)),
                # An example's CV, for this session only; absent means the saved CV.
                cv=str(body.get("cv") or "") or None,
            )
        elif path == "/api/answer/begin":
            self.service.begin_answer()
        elif path == "/api/answer/end":
            self.service.end_answer()
        elif path == "/api/answer/again":
            self.service.retake(
                question=str(body.get("question") or ""),
                use_video=bool(body.get("use_video", True)),
            )
        elif path == "/api/camera/calibrate":
            # Answered here, not with {"ok": true}: the page shows whether it worked and, if not, the reason in the candidate's own terms.
            try:
                seconds = float(body.get("seconds") or 3.0)
            except (TypeError, ValueError):
                seconds = 3.0
            self._send_json(self.service.calibrate(seconds), no_store=True)
            return
        elif path == "/api/difficulty":
            # How demanding an advert reads, for the setup screen.
            # Pure arithmetic on the text: no model, no file, nothing stored.
            import difficulty

            text = str(body.get("text") or "")
            measured = difficulty.measure(text)
            measured["level"] = difficulty.level_for(measured["score"]) if text.strip() else None
            self._send_json(measured, no_store=True)
            return
        elif path == "/api/cv":
            import candidate_cv

            # A missing text field is refused, because treating it as empty would delete the saved CV.
            if "text" not in body:
                self._send_json({"error": "no cv supplied"}, status=400)
                return
            stored = candidate_cv.save(str(body.get("text") or ""))
            self._send_json({"ok": stored,
                             "stored": bool(candidate_cv.load())})
            return
        elif path == "/api/sessions/delete":
            import session_library

            # Hidden from Sessions and the trend; the recordings stay on disk.
            self._send_json({"ok": session_library.delete(str(body.get("name") or ""))})
            return
        elif path == "/api/calendar":
            import interview_calendar

            try:
                if "add" in body:
                    interview_calendar.add(dict(body.get("add") or {}))
                elif "remove" in body:
                    interview_calendar.remove(str(body.get("remove") or ""))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_calendar()
            return
        elif path == "/api/drill":
            self.service.drill(
                dimension=str(body.get("dimension") or ""),
                use_video=bool(body.get("use_video", True)),
            )
        elif path == "/api/reset":
            self.service.reset()
        elif path == "/api/finish":
            self.service.finish()
        elif path == "/api/analyse":
            self.service.analyse(
                job_description=str(body.get("job_description") or ""),
                job_title=str(body.get("job_title") or ""),
            )
        else:
            self._send_json({"error": "no such action"}, status=404)
            return

        self._send_json({"ok": True})

    # Static

    # Readable addresses for the two surfaces.
    # The interview is a place the candidate goes to, not a file they open.
    ROUTES = {"": "index.html", "/": "index.html", "/practise": "app.html",
              # Browsers ask for this whatever the page declares.
              "/favicon.ico": "favicon.svg"}

    def _serve_file(self, path: str) -> None:
        """Send a file from the web folder, using the readable addresses for the two pages."""
        relative = self.ROUTES.get(path.rstrip("/") or "/") or path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()

        # Nothing outside the web directory is servable, whatever the URL says.
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._send_json({"error": "not found"}, status=404)
            return

        if not target.is_file():
            self._send_json({"error": "not found"}, status=404)
            return

        body = target.read_bytes()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # Streams

    def _stream_events(self) -> None:
        """Server-Sent Events: one open response, one message per change."""
        watcher = self.service.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    payload = watcher.get(timeout=15.0)
                except queue.Empty:
                    # A comment line keeps the connection from being reaped by anything in between while nothing is happening.
                    self.wfile.write(b": still here\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(
                    f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except ConnectionError:
            # The tab was closed or reloaded, which is normal.
            pass
        finally:
            self.service.unsubscribe(watcher)

    def _stream_preview(self) -> None:
        """The camera, as multipart MJPEG, straight into an <img>."""
        source: Optional[Callable[[], Optional[bytes]]] = self.server.preview_source
        if source is None:
            self._send_json({"error": "no preview"}, status=503)
            return

        self.send_response(200)
        self.send_header(
            "Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        idle = 0.0
        seen_a_frame = False
        try:
            while True:
                frame = source()
                if frame is None:
                    idle += PREVIEW_INTERVAL_S
                    limit = (PREVIEW_IDLE_TIMEOUT_S if seen_a_frame
                             else PREVIEW_STARTUP_TIMEOUT_S)
                    if idle >= limit:
                        return
                else:
                    idle = 0.0
                    seen_a_frame = True
                    self.wfile.write(
                        f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                threading.Event().wait(PREVIEW_INTERVAL_S)
        except ConnectionError:
            pass  # the page closed the preview


class CoachServer(ThreadingHTTPServer):
    daemon_threads = True  # never block shutdown

    # Off on purpose.
    # On Windows it would let a second server share port 8765, and requests would reach the wrong one. main() explains a failed bind instead.
    allow_reuse_address = False

    def __init__(self, address, service, preview_source=None,
                 camera_status=None):
        super().__init__(address, _Handler)
        self.service = service
        self.preview_source = preview_source
        self.camera_status = camera_status
        # One deck for the server's life, so repeated clicks walk through a level without repeats.
        # Loaded on first use.
        import examples

        self.examples = examples.Deck()

    def handle_error(self, request, client_address) -> None:
        # A closed tab or reload drops the connection, which is not a server error, so no traceback is printed.
        # Real errors still are.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def make_server(service=None, preview_source=None, camera_status=None,
                port: int = DEFAULT_PORT):
    """A server ready to `serve_forever()`.
    Port 0 picks a free one.
    """
    return CoachServer((HOST, port), service or live_service.LiveService(),
                       preview_source, camera_status)


SHOWCASE_DIR = ROOT / "artifacts" / "showcase"

# The saved example sessions.
# One whose file is missing is simply not offered.
SHOWCASE_LABELS = {
    "strong": ("A strong answer",
               "Requirements evidenced, each carrying the candidate's own words."),
    "disfluent": ("A disfluent answer",
                  "Real speech with genuine fillers and pauses, measured."),
    "camera_excluded": ("A fluent answer to a different question",
                        "Capped at 40 by the relevance ceiling: composure and "
                        "delivery cannot substitute for answering. The camera "
                        "found no face, so that channel is excluded and the "
                        "remaining weights renormalised rather than guessed."),
    "no_camera": ("Audio only",
                  "A session run with the camera off, as a candidate may choose."),
}


def list_showcases() -> list:
    """The frozen sessions actually present on disk, in a stable order."""
    found = []
    for name, (label, note) in SHOWCASE_LABELS.items():
        path = SHOWCASE_DIR / f"{name}.json"
        if path.exists():
            found.append({"name": name, "label": label, "note": note,
                          "path": str(path)})
    return found


def readiness() -> dict:
    """What is ready before the candidate starts.

    Checks the language model, microphone and camera first, so a missing Ollama shows up before any time is spent.
    A check that fails itself reports "unknown".
    Only the language model is required.
    """
    checks = []

    try:
        import content_evaluator
        problem = content_evaluator._health_check()
        # The check's own wording is technical, so say plainly what to do instead.
        if problem and "not reachable" in problem:
            problem = "Ollama isn't installed or isn't running. Run the setup file again, or install it from ollama.com and open it."
        elif problem:
            problem = (f"Ollama is running but the model {content_evaluator._MODEL} is missing. "
                       f"Run the setup file again, or run: ollama pull {content_evaluator._MODEL}")
        checks.append({
            "name": "Language model",
            "ok": problem is None,
            "required": True,
            "detail": problem or f"{content_evaluator._MODEL} is ready.",
        })
    except Exception as exc:
        # A broken probe is not a verdict.
        checks.append({"name": "Language model", "ok": None, "required": True,
                       "detail": f"Could not be checked ({exc})."})

    try:
        import sounddevice

        inputs = [d for d in sounddevice.query_devices()
                  if d.get("max_input_channels", 0) > 0]
        checks.append({
            "name": "Microphone",
            "ok": bool(inputs),
            "required": True,
            "detail": (f"{inputs[0]['name']} and {len(inputs) - 1} other input(s)."
                       if len(inputs) > 1 else
                       f"{inputs[0]['name']}." if inputs else
                       "No input device was found."),
        })
    except Exception as exc:
        checks.append({"name": "Microphone", "ok": None, "required": True,
                       "detail": f"Could not be checked ({exc})."})

    vision = ROOT / "vision_env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checks.append({
        "name": "Camera analysis",
        "ok": vision.exists(),
        "required": False,
        "detail": ("The vision environment is present."
                   if vision.exists() else
                   "Not installed. Sessions run audio-only; the camera box "
                   "can be left unticked."),
    })

    blocking = [c for c in checks if c["required"] and c["ok"] is False]
    return {"ready": not blocking, "checks": checks}


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    # Where the camera worker publishes what it sees.
    # Told through the environment, so nothing between here and the worker has to carry it.
    preview_file = Path(tempfile.gettempdir()) / "interview_coach_preview.jpg"
    face_file = Path(f"{preview_file}.json")
    os.environ["COACH_PREVIEW_PATH"] = str(preview_file)

    # Anything left from a previous run is a picture of the last person to use this machine, which must not be shown as though it were live.
    for stale in (preview_file, face_file):
        try:
            stale.unlink()
        except OSError:
            pass

    def read_preview():
        try:
            return preview_file.read_bytes() or None
        except OSError:
            return None

    def read_face():
        try:
            return json.loads(face_file.read_text(encoding="utf-8"))
        except Exception:
            # Absent or mid-write reads as unknown.
            return None

    def warm():
        import gist_transcriber

        gist_transcriber.warm()

    service = live_service.LiveService(on_prepared=warm)
    try:
        server = make_server(service, preview_source=read_preview,
                             camera_status=read_face)
    except OSError as exc:
        print(f"Could not listen on {HOST}:{DEFAULT_PORT} ({exc}).\n"
              "The coach is probably already running: open "
              f"http://{HOST}:{DEFAULT_PORT} instead, or close the other one.")
        return 1
    host, port = server.server_address[:2]
    print(f"Interview coach at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
