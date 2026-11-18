#!/usr/bin/env bash
# Install the local toolchain on macOS (Apple Silicon) and pull the local model.
set -euo pipefail

MODEL="${AIOPS_LLM_MODEL:-qwen2.5:7b-instruct}"

command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
command -v docker >/dev/null || { echo "Install Docker Desktop first and give it ~6 GB RAM."; exit 1; }

for pkg in kind kubectl helm ollama; do
  if ! command -v "$pkg" >/dev/null; then
    echo "installing $pkg"
    brew install "$pkg"
  fi
done

# Ollama runs natively on the Mac (Metal), outside the cluster.
if ! curl -sf http://localhost:11434/api/tags >/dev/null; then
  echo "starting ollama"
  (ollama serve >/tmp/ollama.log 2>&1 &)
  sleep 3
fi
ollama pull "$MODEL"

python3 -m venv .venv
.venv/bin/pip install -q -e '.[dev]'
[ -f config.yaml ] || cp config.example.yaml config.yaml
echo "done. next: make cluster monitoring"
