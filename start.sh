#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
unset PLAYWRIGHT_BROWSERS_PATH
exec .venv/bin/python kwork_parser.py
