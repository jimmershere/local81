# Local-81 RECIPE — "Check my computer for OS, security & virus problems"

A read-only **health & security sweep** you can run on yourself or a fleet,
on demand or on a schedule. Every check is non-mutating; nothing is changed
without you saying so. Local-81 runs the steps, **journals the output**, and
appends a tamper-evident ledger entry so you can prove *when* you last swept.

> **Honesty note (a Local-81 rule):** the OS-update and antivirus steps shell
> out to the standard tools your OS already ships (`apt`/`dnf`, `clamav`,
> `rkhunter`, `lynis`). Local-81 doesn't reinvent a scanner — it orchestrates
> the real ones, captures their output verbatim, and labels which steps are
> read-only vs. which would change the system.

---

## 1. Operator readiness (Local-81, read-only)

```bash
# Prove the box has the basics and nothing is wedged
local81 doctor --fleet            # ssh / python3 / rsync / disk / uptime sweep
local81 status                    # last run + drift at a glance
```

## 2. OS & package hygiene (read-only)

```bash
# Debian/Ubuntu
sudo apt-get update -qq && apt list --upgradable 2>/dev/null   # what's stale
# Fedora/RHEL
sudo dnf check-update || true                                  # exit 100 = updates exist
# Pending reboot? (kernel/libs swapped under you)
[ -f /var/run/reboot-required ] && echo "REBOOT REQUIRED"
```

## 3. Security posture — mapped to recognized control themes

Local-81's `compliance` scanner runs read-only checks themed to AC (access
control), CM (config mgmt), IA (identity), SC (system comms), AU (audit):

```bash
local81 compliance report --scope access     # users, sudo, ssh exposure
local81 compliance report --scope files      # world-writable, SUID drift
local81 compliance report --scope all        # full advisory hardening plan
```

Plus the community standards:

```bash
sudo lynis audit system --quick      # CIS-style hardening score + warnings
sudo rkhunter --check --sk           # rootkit / known-bad signatures
```

## 4. Virus / malware scan (read-only by default)

```bash
sudo freshclam                                   # update virus definitions
# Scan home + common write targets; only REPORT, never auto-delete:
clamscan -r -i --stdout \
    ~/ /tmp /var/tmp /srv \
    --exclude-dir='^/sys|^/proc|^/dev' \
  | tee ~/.local81/logs/clamscan-$(date +%F).log
# (Add --move=~/quarantine to isolate hits — that's the one mutating option,
#  and it's opt-in.)
```

## 5. Prove you swept (tamper-evident)

```bash
local81 audit verify                 # the whole sweep history, hash-chained
local81 audit emit --to "$COLLECTOR" # signed receipt for compliance/insurance
```

---

## Run it on a schedule

Drive this from n8n or cron so a fresh sweep + summary lands every morning:

```bash
# every day at 06:30
30 6 * * *  cd ~/personal && local81 plan --summary && \
            local81 deploy --latest --execute >> ~/.local81/logs/sweep.log 2>&1
```

The whole run — OS, security, virus — ends up as plain text on disk you can
grep, diff, and hand to the next person. No dashboard required.
