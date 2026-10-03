#!/bin/sh
# Start ParcelWatch on 0.0.0.0:8765. Proxy headers let the OAuth redirect use the tunnel's https URL.
cd "$(dirname "$0")"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8765 --proxy-headers --forwarded-allow-ips '*' --timeout-graceful-shutdown 5
