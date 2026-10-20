# Pinned Telegram rich messages build

This fork adds automatic rich-message routing to ZeroClaw PR #11862 at `24946811cc47ad925be4e9563c1a14d107217b0c`.
The PR is open and unmerged at the time of this build.
This is an experimental build, not an official ZeroClaw release.

The application source is pinned to `9b22b54852eb8dc49ed9a439586ce929c218d808`.
Plain text, inline formatting, and code use ordinary Telegram messages.
Headings, lists, quotes, and tables use native rich messages when `rich_messages` is enabled.
The channel selects the message type for each chunk and final draft edit.
The build also changes the release workflow, installer, tests, and documentation.
The build branch replaces inherited workflows with one manual release workflow.
The fork's `master` branch remains unchanged.

## Build

Open **Actions > Build Telegram rich messages release**.
Select the `telegram-rich-auto` branch.
Run the workflow.

GitHub Actions builds the binary on Ubuntu 22.04 for `x86_64-unknown-linux-gnu`.
The workflow uses Rust 1.98.0 and the checked-in `Cargo.lock`.
It keeps the default Cargo features and adds `tools-compat`.
It does not change the release optimization settings.
The build needs no Rust installation on the deployment server.

The workflow tests the installer with fake services and downloads.
A separate job runs the routing tests and existing rich-message tests.
Publication requires both the binary build and the tests to pass.
It also checks the binary's configuration schema.
These checks do not prove that live Telegram messages or MCP connections work.

The publish job uses a separate runner with repository write access.
The build job has read access only.
The workflow does not replace an existing release.
If a publish step fails, inspect the draft release before another attempt.
Do not replace assets from a published release.

## Release files

The release tag is `v0.8.6-telegram-rich-auto-9b22b5485`.
It points to the build commit, which includes the pinned application source and these build files.

The release contains these files:

- `zeroclaw-x86_64-unknown-linux-gnu.tar.gz`: the binary and `BUILDINFO.json`.
- `SHA256SUMS`: the SHA-256 checksum of the archive.
- `BUILDINFO.json`: source commit, build commit, toolchain, target, features, and build URL.

A checksum detects damaged or changed downloads.
It is not a publisher signature.
The archive is not guaranteed to be byte-for-byte reproducible.

## Update the existing server

**The update briefly stops all agents in `zeroclaw.service`.**
Run the command from an SSH session as root.
Do not run it through the ZeroClaw agent, because the update stops that agent's service.
Wait until the release workflow succeeds.

```sh
(f=$(mktemp) && trap 'rm -f "$f"' 0 && curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/LionZXYAgent/zeroclaw/v0.8.6-telegram-rich-auto-9b22b5485/scripts/install-telegram-rich.sh -o "$f" && sh "$f")
```

The installer targets `/root/.cargo/bin/zeroclaw` and `/root/.zeroclaw`.
It requires the existing service to be active.
It checks the archive checksum and embedded source information before stopping the service.
It also checks the binary's Telegram rich messages and MCP certificate settings.

The installer stops the existing service and saves its complete state and old binary.
Backups use `/root/zeroclaw-backups/<timestamp>-<pid>` with owner-only access.
The installer prints the backup path.
Keep that path for rollback.

The installer replaces the binary through an atomic file move.
It enables `channels.telegram.telegram.rich_messages` through the new configuration command.
It keeps the existing keys, memory, schedules, agents, and MCP settings.
It does not run quickstart or create another service.
It restarts the existing service and checks its process for ten seconds.

If configuration or startup fails, the installer restores the previous binary and state.
It also saves the failed state inside the backup directory.
If automatic rollback fails, the installer reports that failure and prints the backup path.

The checks confirm stable process startup, not live connections.
After the update, test a heading, quote, and small table in Telegram.
Check Bitwarden and K3s again.
The Telegram Bot API endpoint must support Bot API 10.1 or later.
Some Telegram clients cannot display these messages.
This build does not add a background-job status scheduler.

## Manual rollback

**Rollback restores the saved state, including its older memory and schedules.**
The commands keep the current state separately.
Run them from an SSH session as root.
Replace the example backup path with the printed path.

```sh
set -eu
backup=/root/zeroclaw-backups/REPLACE-WITH-PRINTED-PATH
test -x "$backup/zeroclaw"
test -d "$backup/state"
systemctl stop zeroclaw.service
install -m 755 "$backup/zeroclaw" /root/.cargo/bin/zeroclaw.rollback
mv -f /root/.cargo/bin/zeroclaw.rollback /root/.cargo/bin/zeroclaw
mv /root/.zeroclaw "$backup/state-before-manual-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "$backup/state" /root/.zeroclaw
systemctl start zeroclaw.service
systemctl is-active zeroclaw.service
```
