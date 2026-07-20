#!/usr/bin/env python3
"""
Idempotent one-time setup: ensure the `frames` and `labels` Kafka topics have
at least MIN_PARTITIONS partitions. Needed for M3 (Kafka partitioning +
cooperative rebalance) — the frames topic historically auto-created with a
single partition, which makes multi-replica consumer-group rebalance
untestable (only one consumer in the group can ever be assigned a partition).

Safe to run repeatedly: creates topics that don't exist yet, and calls
create_partitions() (a non-destructive increase-only operation — existing
messages and partition assignments are retained) on topics with fewer than
MIN_PARTITIONS.

Usage:
  python scripts/ensure_kafka_partitions.py
"""
import os
from kafka.admin import KafkaAdminClient, NewTopic, NewPartitions
from kafka.errors import TopicAlreadyExistsError

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "admin")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "admin-secret")
MIN_PARTITIONS = int(os.getenv("MIN_PARTITIONS", "6"))
TOPICS = ["frames", "labels"]
_DEFAULT_CA_CERT = os.path.join(os.path.dirname(__file__), "..", "certs", "ca.crt")
CA_CERT = os.getenv("CA_CERT", _DEFAULT_CA_CERT)


def main() -> None:
    ssl_kwargs = {"ssl_cafile": CA_CERT} if os.path.exists(CA_CERT) else {}
    security_protocol = "SASL_SSL" if ssl_kwargs else "SASL_PLAINTEXT"
    admin = KafkaAdminClient(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        security_protocol=security_protocol,
        sasl_mechanism="PLAIN",
        sasl_plain_username=KAFKA_SASL_USERNAME,
        sasl_plain_password=KAFKA_SASL_PASSWORD,
        **ssl_kwargs,
    )
    try:
        existing = admin.describe_topics(TOPICS)
        existing_partitions = {
            t["topic"]: len(t["partitions"]) for t in existing if not t.get("error_code")
        }

        to_create = [t for t in TOPICS if t not in existing_partitions]
        if to_create:
            new_topics = [NewTopic(name=t, num_partitions=MIN_PARTITIONS, replication_factor=1)
                          for t in to_create]
            try:
                admin.create_topics(new_topics)
                print(f"Created topics {to_create} with {MIN_PARTITIONS} partitions.")
            except TopicAlreadyExistsError:
                pass

        to_alter = {t: NewPartitions(total_count=MIN_PARTITIONS)
                    for t, n in existing_partitions.items() if n < MIN_PARTITIONS}
        if to_alter:
            admin.create_partitions(to_alter)
            print(f"Increased partitions to {MIN_PARTITIONS} for: {list(to_alter.keys())}")

        if not to_create and not to_alter:
            print(f"All topics already have >= {MIN_PARTITIONS} partitions. Nothing to do.")
    finally:
        admin.close()


if __name__ == "__main__":
    main()
