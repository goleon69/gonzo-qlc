#!/bin/bash
# One-time setup: Python virtual env, libraries, Italian Vosk model (~50 MB)
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt
if [ ! -d model ]; then
  echo "Downloading Italian speech model..."
  curl -L -o model.zip https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip
  unzip -q model.zip
  mv vosk-model-small-it-0.22 model
  rm model.zip
fi
echo "Setup done. Start with: ./run.sh"
