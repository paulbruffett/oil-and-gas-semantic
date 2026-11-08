#!/usr/bin/env bash
# GraphDB Free for the RDF/OWL track's second runtime (ADR 0046). Needs a Docker runtime (Colima:
# `colima start --cpu 4 --memory 6`).
#
#   knowledge/graphdb/graphdb.sh up      # fetch the DuckDB JDBC driver if missing, start GraphDB
#   knowledge/graphdb/graphdb.sh down    # stop and remove the container (the data volume stays)
#   knowledge/graphdb/graphdb.sh reset   # also remove the data volume
#
# Listens on 127.0.0.1:7200 only. Ontop reads the DuckDB file in data/ (built by
# `python -m oag_semantic.graphdb load <dataset_dir>`), mounted read-only.
#
# Version: GraphDB 11 needs a Free licence, requested from Graphwise by email (the built-in Free
# licence was removed in 11.0). Without one this runs 10.8.14, the last licence-free release. Put the
# licence at knowledge/graphdb/graphdb.license (gitignored) and `up` runs 11.5.2 with it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="oag-graphdb"
LICENSE="$HERE/graphdb.license"
if [[ -f "$LICENSE" ]]; then VERSION="11.5.2"; else VERSION="10.8.14"; fi
IMAGE="ontotext/graphdb:$VERSION"
VOLUME="oag-graphdb-home-${VERSION%%.*}"  # one per major version: storage formats differ
JDBC_VERSION="1.5.4.0"  # matches the Python duckdb package (1.5.4), so the file format agrees
JAR="duckdb_jdbc-${JDBC_VERSION}.jar"
MAVEN="https://repo1.maven.org/maven2/org/duckdb/duckdb_jdbc/${JDBC_VERSION}"

fetch_driver() {
  mkdir -p "$HERE/lib/jdbc"
  [[ -f "$HERE/lib/jdbc/$JAR" ]] && return
  curl -sfL "$MAVEN/$JAR" -o "$HERE/lib/jdbc/$JAR.part"
  want="$(curl -sfL "$MAVEN/$JAR.sha1")"
  echo "$want  $HERE/lib/jdbc/$JAR.part" | shasum -a 1 -c - >/dev/null
  mv "$HERE/lib/jdbc/$JAR.part" "$HERE/lib/jdbc/$JAR"
}

case "${1:-}" in
  up)
    fetch_driver
    mkdir -p "$HERE/data"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    license_args=()
    if [[ -f "$LICENSE" ]]; then
      license_args=(-v "$LICENSE:/opt/graphdb/license/graphdb.license:ro")
      java_opts="-Xmx3g -Dgraphdb.license.file=/opt/graphdb/license/graphdb.license"
    else
      java_opts="-Xmx3g"
    fi
    docker run -d --name "$NAME" -p 127.0.0.1:7200:7200 -e GDB_JAVA_OPTS="$java_opts" \
      ${license_args[@]+"${license_args[@]}"} \
      -v "$HERE/lib/jdbc:/opt/graphdb/dist/lib/jdbc:ro" \
      -v "$HERE/data:/data:ro" \
      -v "$VOLUME:/opt/graphdb/home" \
      "$IMAGE" >/dev/null
    for _ in $(seq 1 60); do
      curl -sf http://127.0.0.1:7200/rest/info/version >/dev/null && { echo "GraphDB $VERSION up on :7200"; exit 0; }
      sleep 2
    done
    echo "GraphDB did not start; see: docker logs $NAME" >&2; exit 1 ;;
  down) docker rm -f "$NAME" >/dev/null && echo "stopped" ;;
  reset) docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$VOLUME" >/dev/null && echo "removed" ;;
  *) echo "usage: $0 up|down|reset" >&2; exit 2 ;;
esac
