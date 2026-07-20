#!/bin/sh
set -e

: "${MLFLOW_PROXY_USER:?MLFLOW_PROXY_USER must be set}"
: "${MLFLOW_PROXY_PASSWORD:?MLFLOW_PROXY_PASSWORD must be set}"

printf '%s:%s\n' "$MLFLOW_PROXY_USER" "$(openssl passwd -apr1 "$MLFLOW_PROXY_PASSWORD")" > /etc/nginx/.htpasswd

exec nginx -g 'daemon off;'
