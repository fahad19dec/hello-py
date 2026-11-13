#!/bin/bash
# Runs once, when the PostgreSQL container initialises an empty data directory.
#
# Creates the two application roles and the database:
#   tp_owner  owns the schema and runs migrations. Not a superuser.
#   tp_app    the role the running application uses. It cannot change the
#             schema, does not own tables and cannot bypass row-level security.
#
# The superuser (POSTGRES_USER) is used only here and for backups.
set -euo pipefail

: "${OWNER_DB_PASSWORD:?OWNER_DB_PASSWORD must be set}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"
DB_NAME="${APP_DB_NAME:-tradeplatform}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v owner_password="$OWNER_DB_PASSWORD" -v app_password="$APP_DB_PASSWORD" -v db_name="$DB_NAME" <<'SQL'
CREATE ROLE tp_owner LOGIN PASSWORD :'owner_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE tp_app   LOGIN PASSWORD :'app_password'   NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE :"db_name" OWNER tp_owner;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO tp_app;
SQL
