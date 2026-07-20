#!/bin/bash
# Reclaims Docker disk space accumulated by repeated `docker-compose build`
# runs during development. Age-filtered and dangling-only — never touches
# running containers, their images, named volumes, or anything built/used
# in the last 24h — so it's safe to run after every dev session without
# risking an in-progress build's cache.
#
# Usage: ./scripts/docker_cleanup.sh [--aggressive]
#   --aggressive  also runs `docker image prune -a` (removes ALL unused
#                 images, not just dangling ones) and a full, unfiltered
#                 `docker builder prune` — reclaims more but forces a full
#                 re-pull/rebuild next time. Ask before using in a repo
#                 with in-progress work on other branches.

set -euo pipefail

echo "── Docker disk usage before cleanup ──"
docker system df

echo
echo "── Pruning build cache older than 24h ──"
docker builder prune -f --filter until=24h

echo
echo "── Pruning dangling images ──"
docker image prune -f

if [[ "${1:-}" == "--aggressive" ]]; then
    echo
    echo "── Aggressive mode: pruning ALL unused images + full build cache ──"
    docker image prune -a -f
    docker builder prune -f
fi

echo
echo "── Docker disk usage after cleanup ──"
docker system df
