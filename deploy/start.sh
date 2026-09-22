#!/bin/sh
set -e

# On a fresh volume, pull the database back from B2. On an existing volume, or
# on the very first deploy when there is no backup yet, this does nothing.
litestream restore -config /etc/litestream.yml \
  -if-db-not-exists -if-replica-exists /data/db.sqlite3

# Run the app as a child of Litestream so every write, including migrations,
# is replicated, and so shutdown flushes the last changes before exiting.
exec litestream replicate -config /etc/litestream.yml -exec /app/deploy/serve.sh
