#!/usr/bin/env bash
# Bring the production overlay up for real and prove it serves over TLS.
#
#   scripts/staging_smoke.sh
#
# `docker compose config` only proves the YAML renders. This starts the actual stack and
# asks the questions that a rendered file cannot answer: does TLS terminate, does the
# proxy route both hostnames, does the session cookie carry Secure, and is anything
# reachable that should not be.
#
# It runs against *.localhost, which Caddy serves with its own internal CA — no public
# DNS, no ACME account, no Let's Encrypt rate limit spent on a test. The certificate is
# not publicly trusted, so curl is told to accept it; everything else on the path is the
# same code and the same config that production runs.
#
# Uses its own compose project name, so it can never touch a development stack that is
# already running.

set -euo pipefail

PROJECT=meyraki-staging
COMPOSE="docker compose -p $PROJECT -f docker-compose.yml -f docker-compose.prod.yml"
PASS=0
FAIL=0

export APP_DOMAIN=app.localhost
export API_DOMAIN=api.localhost
export ACME_EMAIL=staging@localhost
export POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-staging-not-a-real-password}
export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-sk-ant-staging-not-a-real-key}
export MEYRAKI_SHARE_SECRET=${MEYRAKI_SHARE_SECRET:-staging-share-secret-long-enough}
export GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

check() {  # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    printf '  PASS  %-58s %s\n' "$1" "$3"; PASS=$((PASS + 1))
  else
    printf '  FAIL  %-58s got %s, want %s\n' "$1" "$3" "$2"; FAIL=$((FAIL + 1))
  fi
}

contains() {  # contains <description> <needle> <haystack>
  if printf '%s' "$3" | grep -qi -- "$2"; then
    printf '  PASS  %-58s %s\n' "$1" "$2"; PASS=$((PASS + 1))
  else
    printf '  FAIL  %-58s missing %s\n' "$1" "$2"; FAIL=$((FAIL + 1))
  fi
}

cleanup() {
  echo
  echo "tearing down $PROJECT…"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "building and starting the production overlay as $PROJECT (sha $GIT_SHA)…"
$COMPOSE up -d --build >/tmp/staging_smoke_build.log 2>&1 || {
  echo "stack failed to start — last 30 lines:"; tail -30 /tmp/staging_smoke_build.log; exit 1; }

echo "waiting for the proxy to answer on TLS…"
for _ in $(seq 1 60); do
  curl -sk --max-time 3 "https://$API_DOMAIN/health" >/dev/null 2>&1 && break
  sleep 2
done

echo
echo "--- TLS and routing ---"
health=$(curl -sk --max-time 10 "https://$API_DOMAIN/health" || echo '{}')
contains "the API answers over HTTPS" '"status":"ok"' "$health"
contains "and reports which build is serving it" "$GIT_SHA" "$health"

app_code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "https://$APP_DOMAIN/login" || echo 000)
check "the web app is served on its own hostname" "200" "$app_code"

# Plain HTTP must not serve content: it should redirect, or the first request of every
# session travels in clear text.
redirect=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://$API_DOMAIN/health" || echo 000)
case "$redirect" in
  30*) printf '  PASS  %-58s %s\n' "plain HTTP redirects rather than serving" "$redirect"; PASS=$((PASS + 1)) ;;
  *)   printf '  FAIL  %-58s got %s, want a 3xx\n' "plain HTTP redirects rather than serving" "$redirect"; FAIL=$((FAIL + 1)) ;;
esac

headers=$(curl -sk -D - -o /dev/null --max-time 10 "https://$API_DOMAIN/health" || true)
contains "HSTS is set" "strict-transport-security" "$headers"
contains "content sniffing is refused" "x-content-type-options" "$headers"

echo
echo "--- the cookie that carries the session ---"
# MEYRAKI_HTTPS=1 is what adds Secure. A terminating proxy does not do it for you, and a
# session cookie without Secure is sent over plain HTTP the first time someone types the
# bare hostname.
cookie=$(curl -sk -D - -o /dev/null --max-time 15 \
  -H 'content-type: application/json' \
  -d "{\"email\":\"staging-$$@test.dev\",\"password\":\"staging-password-1\",\"org_name\":\"Staging\"}" \
  "https://$API_DOMAIN/auth/register" | grep -i '^set-cookie:' || true)
contains "the session cookie is Secure" "secure" "$cookie"
contains "and HttpOnly" "httponly" "$cookie"

echo
echo "--- nothing else is exposed ---"
# Ask Docker what THIS project publishes, rather than probing host ports. The first
# version of this check curled 127.0.0.1:8000 and 127.0.0.1:3000 and reported failures
# that were a developer's own stack answering on the same machine — a test that cannot
# tell its subject from its surroundings reports someone else's success as your failure.
published=$($COMPOSE ps --format '{{.Service}} {{.Publishers}}' 2>/dev/null || true)
printf '%s\n' "$published" | sed 's/^/    /'

for service in api web db redis; do
  line=$(printf '%s\n' "$published" | grep -E "^$service " || true)
  # Compose prints published ports as host:container pairs; an unpublished container
  # shows none. Match a published host port rather than the container port it maps to.
  if printf '%s' "$line" | grep -qE '0\.0\.0\.0:[0-9]+|127\.0\.0\.1:[0-9]+|\[::\]:[0-9]+'; then
    printf '  FAIL  %-58s %s\n' "$service publishes no host port" "$line"; FAIL=$((FAIL + 1))
  else
    printf '  PASS  %-58s none\n' "$service publishes no host port"; PASS=$((PASS + 1))
  fi
done

proxy_ports=$(printf '%s\n' "$published" | grep -E '^proxy ' || true)
for port in 80 443; do
  # `{{.Publishers}}` renders Go structs as {URL TargetPort PublishedPort Protocol}, so a
  # published port reads "0.0.0.0 443 443 tcp" and an unpublished one "{ 8000 0 tcp}" —
  # host port zero. Matching Docker's ":443->" CLI format finds nothing here.
  if printf '%s' "$proxy_ports" | grep -qE "0\.0\.0\.0 $port $port"; then
    printf '  PASS  %-58s %s\n' "the proxy publishes $port" "$port"; PASS=$((PASS + 1))
  else
    printf '  FAIL  %-58s not found in %s\n' "the proxy publishes $port" "$proxy_ports"; FAIL=$((FAIL + 1))
  fi
done

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
