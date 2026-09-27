# app.py
"""Starts Callback and opens it in the browser.

Usage: python app.py (from the project folder, or press Run on this file in your editor).
It always runs with the project's own Python in .venv, which setup creates.
Close the terminal, or press Ctrl+C, to stop it.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
ADDRESS = "http://127.0.0.1:8765"
OLLAMA = "http://127.0.0.1:11434/api/version"


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA, timeout=2):
            return True
    except OSError:
        return False


def start_ollama() -> None:
    """Start Ollama if it is installed but closed, and wait briefly for it.

    If it is not installed, nothing happens here: the setup screen says so and how to fix it.
    """
    if ollama_running():
        return
    windows_app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
    if os.name == "nt" and windows_app.exists():
        subprocess.Popen([str(windows_app)], creationflags=subprocess.DETACHED_PROCESS)
    elif sys.platform == "darwin" and subprocess.call(["open", "-g", "-a", "Ollama"], stderr=subprocess.DEVNULL) == 0:
        pass
    elif shutil.which("ollama"):
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        return
    print("Starting Ollama...")
    for _ in range(15):
        if ollama_running():
            return
        time.sleep(1)


def main() -> int:
    if not VENV_PYTHON.exists():
        print("Callback is not set up yet. Run setup.bat on Windows, or bash setup.sh on a Mac, then run this again.")
        return 1

    # An editor may run this file with another Python, which lacks the project's libraries.
    if Path(sys.prefix).resolve() != VENV.resolve():
        return subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

    # Python from python.org has no certificates of its own on a Mac, which stops the speech models downloading.
    if sys.platform == "darwin":
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())

    start_ollama()
    sys.path.insert(0, str(ROOT / "backend"))
    import web_server

    threading.Timer(3.0, webbrowser.open, [ADDRESS]).start()
    return web_server.main()


if __name__ == "__main__":
    raise SystemExit(main())
