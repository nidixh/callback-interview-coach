#!/bin/bash
# setup.sh
# Callback setup for a Mac with Apple Silicon.
# In a terminal opened in the project folder (the one built into your editor is fine), run:
#   bash setup.sh
# It installs whatever is missing, Python 3.11 and Ollama included, then the libraries and every model, and opens the app.
# Safe to run again: finished steps are skipped or simply repeated.

set -e
cd "$(dirname "$0")"

step() { printf '\n\033[33m== %s\033[0m\n' "$1"; }

# Python 3.11 on the PATH, or where the python.org installer puts it.
find_python() {
    for candidate in python3.11 /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 python3; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if [ "$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" = "3.11" ]; then
            echo "$candidate"; return
        fi
    done
}

find_ollama() {
    if command -v ollama >/dev/null 2>&1; then command -v ollama; return; fi
    for app in /Applications "$HOME/Applications"; do
        if [ -x "$app/Ollama.app/Contents/Resources/ollama" ]; then echo "$app/Ollama.app/Contents/Resources/ollama"; return; fi
    done
}

ollama_running() { curl -s -o /dev/null http://127.0.0.1:11434/api/version; }

if [ "$(uname -m)" != "arm64" ]; then
    echo "This Mac has an Intel processor. On a Mac, Callback needs Apple Silicon (M1 or newer),"
    echo "because the speech libraries it uses are no longer built for Intel Macs."
    exit 1
fi

step "Python 3.11"
PY="$(find_python)"
if [ -z "$PY" ]; then
    echo "Not found, so installing it from python.org. macOS asks for your password to allow this."
    curl -fL -o /tmp/python-3.11.9-macos11.pkg https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg
    sudo installer -pkg /tmp/python-3.11.9-macos11.pkg -target /
    PY="$(find_python)"
    if [ -z "$PY" ]; then
        echo "Python 3.11 did not install. Install it from https://www.python.org/downloads/release/python-3119/ and run this again."
        exit 1
    fi
fi
echo "Ready."

step "Speech and scoring environment (.venv)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install setuptools==80.9.0 wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install openai-whisper==20240930 --no-build-isolation

step "Camera environment (vision_env)"
[ -x vision_env/bin/python ] || "$PY" -m venv vision_env
vision_env/bin/python -m pip install --upgrade pip
vision_env/bin/python -m pip install -r requirements-vision.txt
vision_env/bin/python -m pip install --no-deps mediapipe==1.0.1

step "Speech models (Whisper and the speaker model)"
# Python from python.org has no certificates of its own on a Mac; use the bundled ones.
export SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')"
.venv/bin/python backend/fetch_models.py

step "Language models (Ollama)"
OLLAMA="$(find_ollama)"
if [ -z "$OLLAMA" ]; then
    echo "Ollama not found, so installing it."
    APPS=/Applications
    [ -w "$APPS" ] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; }
    curl -fL -o /tmp/Ollama-darwin.zip https://ollama.com/download/Ollama-darwin.zip
    unzip -q -o /tmp/Ollama-darwin.zip -d "$APPS"
    OLLAMA="$(find_ollama)"
    if [ -z "$OLLAMA" ]; then
        echo "Ollama did not install. Install it from https://ollama.com/download and run this again."
        exit 1
    fi
fi
# Models are fetched through Ollama's background service; start it if it is not running yet.
if ! ollama_running; then
    open -g -a Ollama 2>/dev/null || ("$OLLAMA" serve >/dev/null 2>&1 &)
    for _ in $(seq 30); do ollama_running && break; sleep 2; done
fi
"$OLLAMA" pull qwen3:4b
"$OLLAMA" pull qwen2.5:7b

chmod +x start.command
step "Done"
printf '\033[32mOpening Callback. Next time, run: bash start.command\033[0m\n'
exec bash start.command
