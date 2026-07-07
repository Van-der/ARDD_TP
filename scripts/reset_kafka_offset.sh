#!/usr/bin/env bash
# Reset Kafka consumer group offsets to latest, dropping any accumulated backlog.
# Run this before a benchmark or eval run to avoid processing stale frames.
#
# Usage:
#   ./scripts/reset_kafka_offset.sh                    # reset aggregation + temporal
#   ./scripts/reset_kafka_offset.sh aggregation-only   # reset aggregation only
#   ./scripts/reset_kafka_offset.sh temporal-only      # reset temporal only

set -euo pipefail

MODE="${1:-both}"

# Write properties directly into the container (avoids NTFS tmpfile encoding issues)
docker exec kafka bash -c 'cat > /tmp/client.properties <<PROPS
security.protocol=SASL_PLAINTEXT
sasl.mechanism=PLAIN
sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="admin" password="admin-secret";
PROPS'

echo "Stopping services to release consumer groups..."
if [[ "$MODE" != "temporal-only" ]]; then
    docker stop aggregation-service 2>/dev/null || true
fi
if [[ "$MODE" != "aggregation-only" ]]; then
    docker stop temporal-service 2>/dev/null || true
fi

sleep 5

echo "Resetting offsets to latest..."
if [[ "$MODE" != "temporal-only" ]]; then
    docker exec kafka kafka-consumer-groups \
        --bootstrap-server localhost:9092 \
        --group aggregation-pipeline-group \
        --topic frames \
        --reset-offsets --to-latest --execute \
        --command-config /tmp/client.properties
    echo "  ✓ aggregation-pipeline-group reset"
fi
if [[ "$MODE" != "aggregation-only" ]]; then
    docker exec kafka kafka-consumer-groups \
        --bootstrap-server localhost:9092 \
        --group temporal-service-group \
        --topic frames \
        --reset-offsets --to-latest --execute \
        --command-config /tmp/client.properties
    echo "  ✓ temporal-service-group reset"
fi

echo "Restarting services..."
if [[ "$MODE" != "temporal-only" ]]; then
    docker start aggregation-service
fi
if [[ "$MODE" != "aggregation-only" ]]; then
    docker start temporal-service
fi

echo "Waiting 12s for services to reconnect..."
sleep 12

echo "Done. Consumer lag:"
for g in aggregation-pipeline-group temporal-service-group; do
    echo "  --- $g ---"
    docker exec kafka kafka-consumer-groups \
        --bootstrap-server localhost:9092 \
        --group "$g" --describe \
        --command-config /tmp/client.properties 2>/dev/null \
        | grep -E "TOPIC|frames" || echo "  (no committed offset yet — service reconnecting)"
done
