#!/bin/sh
# docker-entrypoint.sh — merge custom CA certificates then exec the real command.
#
# To add custom root certificates, mount a PEM-encoded bundle to:
#   /etc/phalanx/certs/ca-bundle.crt
#
# The merged bundle is written to /etc/ssl/certs/phalanx-ca-bundle.crt and
# REQUESTS_CA_BUNDLE / SSL_CERT_FILE are set so that:
#   - the `requests` library (used by python-gitlab, PyGithub, etc.)
#   - the `httpx` library
#   - the `ssl` stdlib module
# all trust the additional certificates.

set -e

CUSTOM_CERT=/etc/phalanx/certs/ca-bundle.crt
MERGED_BUNDLE=/etc/ssl/certs/phalanx-ca-bundle.crt

if [ -f "$CUSTOM_CERT" ]; then
    # Start with the system bundle, append custom certs
    SYSTEM_BUNDLE=$(python3 -c "import certifi; print(certifi.where())" 2>/dev/null || echo "/etc/ssl/certs/ca-certificates.crt")
    cat "$SYSTEM_BUNDLE" "$CUSTOM_CERT" > "$MERGED_BUNDLE"
    export REQUESTS_CA_BUNDLE="$MERGED_BUNDLE"
    export SSL_CERT_FILE="$MERGED_BUNDLE"
fi

exec "$@"
