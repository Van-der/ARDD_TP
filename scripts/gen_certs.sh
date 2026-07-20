#!/usr/bin/env bash
# Generates a local self-signed root CA plus per-service leaf certs for M10's
# mTLS mesh. Re-run any time to regenerate everything from scratch (cert
# rotation in this project is restart-based, not hot-reload — see PLAN/SECURITY.md).
#
# Runs openssl inside a throwaway `alpine/openssl` container rather than
# requiring openssl on the host (not installed here by default).
#
# Internal mesh (mutual TLS, client cert required): vision-service, rag-agent,
# temporal-service, kafka.
# Browser-facing (server-auth TLS, client cert optional): aggregation-service
# — it's the one service the React dashboard and host-side scripts hit
# directly, so it can't require a client cert without breaking the browser.
# webhook-receiver and mlflow are intentionally NOT in this mesh: mlflow's
# `mlflow server` CLI has no TLS flags at all (would need a reverse-proxy
# sidecar, disproportionate for internal-only experiment tracking), and
# webhook-receiver stands in for an arbitrary external target (e.g. a real
# Slack webhook), which by design is never mTLS.
set -euo pipefail

CERT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/certs"
DAYS=3650  # 10 years — local demo CA, not a production rotation policy

mkdir -p "$CERT_DIR"

docker run --rm -v "$CERT_DIR:/certs" -w /certs --entrypoint sh alpine/openssl -c "
set -euo pipefail
DAYS=$DAYS

echo '==> Generating root CA'
openssl genrsa -out ca.key 4096 2>/dev/null
# basicConstraints + keyUsage are required explicitly — newer OpenSSL/Python
# ssl (though not curl) reject a CA cert missing keyUsage's keyCertSign bit
# during strict chain validation (RFC 5280).
openssl req -x509 -new -nodes -key ca.key -sha256 -days \$DAYS \
    -subj '/CN=ARDD-TP Local Dev CA' \
    -addext 'basicConstraints=critical,CA:true' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -out ca.crt 2>/dev/null

for svc in vision-service rag-agent aggregation-service temporal-service kafka; do
    echo \"==> Generating cert for \$svc\"
    openssl genrsa -out \"\${svc}.key\" 2048 2>/dev/null
    cat > \"\${svc}.ext\" <<EXT
subjectAltName = DNS:\$svc, DNS:localhost, IP:127.0.0.1
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth,clientAuth
EXT
    openssl req -new -key \"\${svc}.key\" -subj \"/CN=\${svc}\" -out \"\${svc}.csr\" 2>/dev/null
    openssl x509 -req -in \"\${svc}.csr\" -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out \"\${svc}.crt\" -days \$DAYS -sha256 -extfile \"\${svc}.ext\" 2>/dev/null
    rm -f \"\${svc}.csr\" \"\${svc}.ext\"
done

# Kafka's PEM keystore format (KIP-651, ssl.keystore.type=PEM) wants the leaf
# cert and private key concatenated into a single file.
cat kafka.crt kafka.key > kafka-keystore.pem

chmod 644 /certs/*.crt
chmod 644 /certs/*.key
chmod 644 /certs/*.pem
"

echo
echo "==> Done. Certs written to $CERT_DIR"
echo
echo "To trust the CA in a browser (needed for the dashboard's https/wss connection"
echo "to aggregation-service, e.g. https://localhost:8003), import ca.crt once:"
echo "  Chrome/Edge:  chrome://settings/certificates -> Authorities -> Import -> $CERT_DIR/ca.crt"
echo "  Windows cert store: certutil -addstore -f ROOT $CERT_DIR/ca.crt"
echo "  Linux (Firefox uses its own store; for curl/system trust):"
echo "    sudo cp $CERT_DIR/ca.crt /usr/local/share/ca-certificates/ardd-tp-ca.crt && sudo update-ca-certificates"
