#!/bin/bash
# start.command
# Starts Callback on a Mac and opens it in the browser.
# Close this window to stop it.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Callback is not set up yet. Run setup.sh first:"
    echo "    bash setup.sh"
    read -r -p "Press Return to close." _
    exit 1
fi
# Python from python.org has no certificates of its own on a Mac, which stops the speech models downloading on the first run.
# Point it at the bundled ones.
export SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi; print(certifi.where())')"
.venv/bin/python app.py
