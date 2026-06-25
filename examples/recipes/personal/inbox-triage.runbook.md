# Local-81 RECIPE — "Read & flag my important email, then brief me daily"

Every morning: scan the inbox, **label** what matters, **move urgent mail** to
the right box, and drop a **one-page summary** in your inbox (or Slack). You
read three bullets instead of three hundred messages.

> **Honesty note:** Local-81 is the *runbook runner* here — it triggers the job
> on schedule, captures every action it took, and journals + audits the run so
> you can see exactly what got relabeled and why. The Gmail work itself is done
> by a small, auditable helper (`gog`, the Google Workspace CLI) talking to the
> Gmail API with **your** OAuth token, which stays in your keychain — never in
> this runbook, never in a Local-81 artifact.

---

## What it does each run

1. **Classify** unread threads (rules first, optional LLM for the fuzzy ones):
   - from a VIP sender / your boss / a known vendor → `Important`
   - payment, invoice, "action required", legal, security alert → `Urgent`
   - newsletters, receipts, calendar noise → `FYI` (and skip the inbox)
2. **Label & move**: apply the label, move `Urgent` out of the firehose into a
   dedicated box, archive `FYI`.
3. **Summarize**: write a dated digest — counts per label, the 5 things that
   actually need you, and any deadlines detected.

## The job (one script Local-81 schedules)

```bash
#!/usr/bin/env bash
# inbox-triage.sh — invoked by `local81 deploy` as a runbook step.
set -euo pipefail

# 1) ensure the labels exist (idempotent)
for L in Important Urgent FYI; do gog gmail labels ensure "$L"; done

# 2) classify unread, last 24h. Rules in triage-rules.yaml; --dry-run prints
#    the planned moves without touching anything (Local-81 dry-run-first ethos).
gog gmail search --query "is:unread newer_than:1d" --json \
  | python3 triage.py --rules triage-rules.yaml ${DRY_RUN:+--dry-run} \
  | tee ~/.local81/logs/triage-$(date +%F).jsonl
#    triage.py emits one action per line; on --execute it calls:
#      gog gmail label   <thread> --add Urgent
#      gog gmail move    <thread> --to Urgent
#      gog gmail archive <thread>

# 3) daily brief -> drafts a summary email back to yourself
gog gmail search --query "newer_than:1d (label:Urgent OR label:Important)" --json \
  | python3 brief.py \
  | gog gmail draft --to me --subject "Daily inbox brief — $(date +%F)"
```

`triage-rules.yaml` is the readable policy you own:

```yaml
vip_senders:   [boss@work.com, "@ourbank.com", spouse@home.net]
urgent_terms:  [invoice, "payment due", "action required", overdue, "security alert"]
fyi_senders:   ["@substack.com", "noreply@", "@newsletter"]
llm_fallback:  true     # only for threads no rule matched; off = pure rules
```

## Wire it up

```bash
# one-time: authorize gog against your Google account (token -> OS keychain)
gog auth login --scopes gmail.modify

# preview a run — labels nothing, just prints the plan
DRY_RUN=1 local81 --profile inbox-triage deploy --latest --execute

# go live, every weekday at 07:00 (cron or the n8n schedule trigger)
0 7 * * 1-5  cd ~/inbox && local81 --profile inbox-triage deploy --latest --execute \
             >> ~/.local81/logs/inbox.log 2>&1
```

## Why run it through Local-81 at all?

Because the value of an inbox bot is *trust*: with Local-81 you get a dry-run
preview before anything moves, a plain-text journal of every relabel/move, a
tamper-evident ledger entry per run (`local81 audit verify`), and a one-line
rollback hint if a rule misfires — instead of a black box silently reorganizing
your mail.
