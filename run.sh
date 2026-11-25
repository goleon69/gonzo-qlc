#!/bin/bash
# Run Gonzo from source (macOS / Linux) after ./setup.sh
cd "$(dirname "$0")"
source .venv/bin/activate
python gonzo_app.py "$@"
