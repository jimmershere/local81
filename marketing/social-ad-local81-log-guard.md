# Local-81 Log Guard — Launch Copy

> Status: DRAFT for human review. Nothing here is posted. Do not publish without sign-off (see checklist at bottom).

---

## 1. Positioning lines / taglines

- Treat logs like untrusted input — because they are.
- Tamper-evidence and injection defense for the logs your operators (and your agents) read.
- Logs are an attack surface. Log Guard makes them safer to read — by humans and AI agents.
- Integrity proves the bytes didn't change. Sanitization stops what those bytes try to do. Log Guard ships both.

---

## 2. LinkedIn post

Announcing Local-81 Log Guard — a new capability in the open-source Local-81 deploy/runbook control plane.

Logs pulled back from remote hosts are an untrusted input channel. When an operator — or increasingly an AI coding agent — is asked to triage them, a planted prompt-injection payload can turn "read this error report" into "run this attacker command." That class of attack is sometimes called agentjacking, or log-injection.

Log Guard applies two always-on controls before any log is consumed:

1. Merkle integrity — a SHA-256 manifest under an RFC-6962 Merkle root, so you can verify bytes are unchanged since capture.
2. Injection scanning and sanitization — strips ANSI/terminal escapes, hidden and bidirectional Unicode, and Unicode tag-char smuggling, and flags prompt-injection phrasing (jailbreaks, tool-call markup, secret exfiltration, curl|sh and package-install RCE, base64 obfuscation).

Honest framing matters: integrity checks alone do not stop injection — sanitization does. We ship both.

Built with help from Claude. Open source. Read the writeup.

#OpenSource #DevSecOps #AISecurity #PromptInjection #SRE

---

## 3. X / Twitter thread

**1/**
New in open-source Local-81: Log Guard.

Logs from remote hosts are untrusted input. When an AI agent is asked to triage them, a planted prompt-injection payload can turn "read this log" into "run this command."

Two always-on controls. Honest about what each one does. 🧵

**2/**
Control 1 — Merkle integrity.

A SHA-256 manifest under an RFC-6962 Merkle root. Tamper-evidence that the bytes are unchanged since capture.

But integrity alone does NOT stop injection. A malicious payload can be perfectly intact. That's where most "log security" stops.

**3/**
Control 2 — Injection scanning + sanitization.

Strips ANSI/terminal escapes, hidden/bidi Unicode, and Unicode tag-char smuggling. Flags prompt-injection phrasing: jailbreaks, tool-call markup, secret exfiltration, curl|sh & package-install RCE, base64 obfuscation.

**4/**
The threat has a name: agentjacking / log-injection. Attacker plants the payload in a log or error report; an AI agent asked to help executes it.

Log Guard treats the log channel as hostile by default — before a human or an agent reads it.

**5/**
Open source. Built with help from Claude.

Integrity proves the bytes didn't change. Sanitization stops what they try to do. Log Guard ships both.

Read the writeup → [link]

---

## 4. Mastodon / Bluesky variant

New in open-source Local-81: Log Guard.

Logs from remote hosts are untrusted input. A planted prompt-injection payload can turn "AI, triage this error" into "AI, run this command" (aka agentjacking).

Two always-on controls: Merkle integrity (RFC-6962, tamper-evidence) + injection sanitization (ANSI/bidi/tag-char stripping, payload flagging). Integrity alone doesn't stop injection — sanitization does. We ship both.

Writeup → [link]

---

## 5. Boosted ad variant (with CTA)

Logs are an untrusted input channel — and an AI agent asked to triage them can be tricked into running attacker commands (agentjacking). Local-81 Log Guard adds two always-on defenses to your open-source deploy/runbook control plane: RFC-6962 Merkle integrity for tamper-evidence, plus injection scanning and sanitization that strips terminal escapes and hidden Unicode and flags prompt-injection payloads. Read the writeup and try Log Guard.

CTA options: Read the writeup · Try Local-81 Log Guard · Read the AI-IT alerts page

---

## 6. Disclosure & review checklist

Before any of this is published, a human must:

- [ ] Read and approve the final wording. Nothing in this file is posted automatically — a person publishes it manually in each platform.
- [ ] Confirm no performance, adoption, or efficacy metrics have been added without supporting evidence. This draft intentionally contains zero numeric claims.
- [ ] Confirm there are no endorsements, testimonials, or third-party logos that haven't been authorized in writing.
- [ ] Verify all factual claims against the actual Local-81 Log Guard capability and the writeup before publishing.
- [ ] Confirm the "built with help from Claude" attribution is phrased accurately and is acceptable to publish.
- [ ] Replace every [link] placeholder with the correct, reviewed destination URL.
- [ ] Set ad spend, budget, audience, and targeting manually in the ad platform. None of that is configured here; a human owns those decisions.
- [ ] Confirm disclosure/compliance requirements (e.g. sponsored/ad labeling) for each platform are met.
