#!/bin/sh
# Install the pinned automatic rich-message routing release on the existing systemd host.
# Run this script over SSH. Do not run it through the ZeroClaw agent.
set -eu
umask 077

main() {
    REPOSITORY=LionZXYAgent/zeroclaw
    RELEASE_TAG=v0.8.6-telegram-rich-auto-07bb9f629
    SOURCE_COMMIT=07bb9f629c5824e188a4188b981f523befc23bb4
    ASSET=zeroclaw-x86_64-unknown-linux-gnu.tar.gz
    BASE_URL="https://github.com/$REPOSITORY/releases/download/$RELEASE_TAG"

    # These path overrides also let the automated tests use an isolated directory.
    BIN=${ZEROCLAW_BIN:-/root/.cargo/bin/zeroclaw}
    DATA=${ZEROCLAW_DATA_DIR:-/root/.zeroclaw}
    SERVICE=${ZEROCLAW_SERVICE:-zeroclaw.service}
    BACKUP_ROOT=${ZEROCLAW_BACKUP_ROOT:-/root/zeroclaw-backups}
    LOCK=${ZEROCLAW_UPDATE_LOCK:-/run/lock/zeroclaw-telegram-rich-update.lock}
    HEALTH_DELAY=${ZEROCLAW_UPDATE_HEALTH_DELAY:-2}

    fail() { printf '%s\n' "$*" >&2; exit 1; }
    [ "$(id -u)" = 0 ] || fail 'Run this installer as root.'
    [ "$(uname -s)" = Linux ] || fail 'This release requires Linux.'
    [ "$(uname -m)" = x86_64 ] || fail 'This release requires x86_64.'
    for command in curl python3 systemctl install cp mv mktemp flock sha256sum; do
        command -v "$command" >/dev/null 2>&1 || fail "Required command is missing: $command"
    done
    [ -x "$BIN" ] || fail "The existing binary is missing: $BIN"
    [ -f "$DATA/config.toml" ] || fail "The existing configuration is missing: $DATA/config.toml"
    [ ! -L "$DATA" ] || fail 'The data directory must not be a symbolic link.'
    [ ! -L "$BIN" ] || fail 'The binary must not be a symbolic link.'
    case "$BIN:$DATA:$BACKUP_ROOT:$LOCK" in
        /*:/*:/*:/*) ;;
        *) fail 'The binary, data, backup, and lock paths must be absolute.' ;;
    esac
    case "$BACKUP_ROOT/" in "$DATA/"*) fail 'Keep the backup directory outside the data directory.' ;; esac
    case "$HEALTH_DELAY" in ''|*[!0-9]*) fail 'The health delay must be a whole number.' ;; esac

    exec 9>"$LOCK"
    flock -n 9 || fail 'Another ZeroClaw update is running.'
    systemctl is-active --quiet "$SERVICE" || fail "The service is not active: $SERVICE"
    exec_start=$(systemctl show "$SERVICE" --property=ExecStart --value)
    printf '%s\n' "$exec_start" | python3 -c '
import re, sys
match = re.search(r"\bpath=([^ ;}]+)", sys.stdin.read())
if match is None or match.group(1) != sys.argv[1]:
    sys.exit("The service does not use the expected binary. No files changed.")
' "$BIN"

    TMP=$(mktemp -d)
    BACKUP=
    STOPPED=0
    SAVED=0
    CHANGED=0
    DONE=0
    NEW="${BIN}.telegram-rich-new-$$"

    restore() {
        printf '%s\n' 'The update failed. Restoring the previous binary and state.' >&2
        systemctl stop "$SERVICE" || return 1
        install -m 755 "$BACKUP/zeroclaw" "$NEW" || return 1
        mv -f "$NEW" "$BIN" || return 1
        if [ -e "$DATA" ]; then
            mv "$DATA" "$BACKUP/state-after-failed-update" || return 1
        fi
        cp -a "$BACKUP/state" "$DATA" || return 1
        systemctl start "$SERVICE" || return 1
        systemctl is-active --quiet "$SERVICE"
    }

    finish() {
        result=$?
        trap - 0 1 2 15
        set +e
        if [ "$DONE" != 1 ] && [ "$STOPPED" = 1 ]; then
            if [ "$SAVED" = 1 ] && [ "$CHANGED" = 1 ]; then
                if ! restore; then
                    printf 'Automatic rollback failed. Backup: %s\n' "$BACKUP" >&2
                    result=1
                fi
            elif ! systemctl start "$SERVICE"; then
                printf 'The old service did not restart. Backup: %s\n' "$BACKUP" >&2
                result=1
            fi
        fi
        if [ "$DONE" != 1 ] && [ "$result" = 0 ]; then result=1; fi
        rm -f "$NEW"
        rm -rf "$TMP"
        exit "$result"
    }
    trap finish 0
    trap 'exit 129' 1
    trap 'exit 130' 2
    trap 'exit 143' 15

    printf 'Downloading %s from %s\n' "$RELEASE_TAG" "$REPOSITORY"
    for file in "$ASSET" SHA256SUMS; do
        curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
            --retry 3 --connect-timeout 15 --max-time 600 \
            "$BASE_URL/$file" --output "$TMP/$file"
    done

    python3 - "$TMP" "$ASSET" "$REPOSITORY" "$RELEASE_TAG" "$SOURCE_COMMIT" <<'PY'
import hashlib, hmac, json, os, pathlib, re, sys, tarfile
root = pathlib.Path(sys.argv[1])
asset, repository, tag, source = sys.argv[2:]
manifest = (root / "SHA256SUMS").read_text().strip()
match = re.fullmatch(r"([0-9a-f]{64})  " + re.escape(asset), manifest)
if match is None:
    sys.exit("The checksum file has an unexpected format.")
digest = hashlib.sha256((root / asset).read_bytes()).hexdigest()
if not hmac.compare_digest(digest, match.group(1)):
    sys.exit("The archive checksum does not match. The service is unchanged.")
payload = root / "payload"
payload.mkdir()
with tarfile.open(root / asset, "r:gz") as archive:
    members = archive.getmembers()
    if len(members) != 2 or {m.name for m in members} != {"zeroclaw", "BUILDINFO.json"}:
        sys.exit("The archive contains unexpected files.")
    for member in members:
        if not member.isfile():
            sys.exit("The archive contains a non-regular file.")
        with archive.extractfile(member) as stream:
            (payload / member.name).write_bytes(stream.read())
info = json.loads((payload / "BUILDINFO.json").read_text())
expected = {"repository": repository, "release_tag": tag,
            "source_commit": source, "target": "x86_64-unknown-linux-gnu"}
if any(info.get(key) != value for key, value in expected.items()):
    sys.exit("The archive does not match the pinned release.")
os.chmod(payload / "zeroclaw", 0o755)
print("The archive checksum and source revision match.")
PY

    "$TMP/payload/zeroclaw" --version
    "$TMP/payload/zeroclaw" config schema > "$TMP/schema.json"
    python3 - "$TMP/schema.json" <<'PY'
import json, sys
schema = json.load(open(sys.argv[1]))["$defs"]
if schema["TelegramConfig"]["properties"]["rich_messages"]["type"] != "boolean":
    sys.exit("The binary does not support Telegram rich messages.")
if "tls_ca_cert_path" not in schema["McpServerConfig"]["properties"]:
    sys.exit("The binary does not support the existing MCP certificate setting.")
PY

    install -d -m 700 "$BACKUP_ROOT"
    BACKUP="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    install -d -m 700 "$BACKUP"
    printf 'Backup directory: %s\n' "$BACKUP"

    STOPPED=1
    systemctl stop "$SERVICE"
    cp -a "$DATA" "$BACKUP/state"
    cp -p "$BIN" "$BACKUP/zeroclaw"
    SAVED=1

    CHANGED=1
    install -m 755 "$TMP/payload/zeroclaw" "$NEW"
    mv -f "$NEW" "$BIN"
    "$BIN" --config-dir "$DATA" config set \
        channels.telegram.telegram.rich_messages true --no-interactive
    systemctl start "$SERVICE"
    pid=$(systemctl show "$SERVICE" --property=MainPID --value)
    [ "$pid" != 0 ] || fail 'The new service did not start.'
    for attempt in 1 2 3 4 5; do
        sleep "$HEALTH_DELAY"
        systemctl is-active --quiet "$SERVICE" || fail 'The new service stopped.'
        [ "$(systemctl show "$SERVICE" --property=MainPID --value)" = "$pid" ] \
            || fail 'The new service restarted during the startup test.'
    done

    DONE=1
    printf '%s\n' 'The update succeeded. Telegram rich messages are enabled.'
    printf 'The previous binary and full state remain in %s\n' "$BACKUP"
    printf '%s\n' 'Test a Telegram table and the existing MCP connections.'
}

main "$@"
