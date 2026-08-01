#!/usr/bin/env bash
# DuckDNS updater — points <sub>.duckdns.org at this VPS's current public IP.
#
# DuckDNS resolves the caller's source IP automatically when ip= is left empty,
# so this works behind most NAT setups without hardcoding an address.
#
# Config is read from an environment file (default: /etc/duckdns/duckdns.env)
# that MUST NOT be world-readable (it holds your DuckDNS token):
#
#   DUCKDNS_DOMAINS="mysub"          # subdomain(s), comma-separated, NO ".duckdns.org"
#   DUCKDNS_TOKEN="xxxxxxxx-xxxx-..."# from https://www.duckdns.org (your account)
#
#   sudo install -m 600 /dev/null /etc/duckdns/duckdns.env   # then edit it
#
# Run manually to test:  DUCKDNS_ENV=/etc/duckdns/duckdns.env ./duckdns-update.sh
# Scheduled via the systemd timer (duckdns.timer) every 5 minutes.

set -euo pipefail

ENV_FILE="${DUCKDNS_ENV:-/etc/duckdns/duckdns.env}"

if [[ ! -r "$ENV_FILE" ]]; then
	echo "duckdns-update: config file not readable: $ENV_FILE" >&2
	exit 1
fi
# shellcheck source=/dev/null
source "$ENV_FILE"

: "${DUCKDNS_DOMAINS:?DUCKDNS_DOMAINS not set in $ENV_FILE}"
: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN not set in $ENV_FILE}"

# ip= is intentionally empty: DuckDNS uses the request's source IP.
response="$(curl -fsS --max-time 20 \
	"https://www.duckdns.org/update?domains=${DUCKDNS_DOMAINS}&token=${DUCKDNS_TOKEN}&ip=" \
	|| true)"

if [[ "$response" == "OK" ]]; then
	echo "duckdns-update: OK (${DUCKDNS_DOMAINS})"
	exit 0
fi

# "KO" means bad token/domain; empty means network/HTTP failure.
echo "duckdns-update: FAILED (response='${response:-<none>}')" >&2
exit 1
