# portwright.io — static page artifacts

This directory holds **static web page artifacts** for the public site
**portwright.io**. They are plain, self-contained HTML files with inline CSS and
no external assets or build step.

## Contents

- `ai-it-alerts.html` — the "AI / IT Security Alerts" news-and-remediation page.
  It is an **editorial / educational** page covering the *Agentjacking* (AI-agent
  log-injection) threat class and the **Local-81 Log Guard** defensive controls.

## Important: this is NOT auto-deployed

These files live in the Local-81 repository for authoring and review only.
**Nothing in this repo publishes them to portwright.io.** There is no CI job,
deploy hook, or pipeline in this repository that pushes `site/portwright/` to the
live site. Editing or merging these files has no effect on production until an
operator explicitly publishes them (see below).

## How an operator publishes a page

1. Copy the desired file(s) out of `site/portwright/` into the portwright.io
   site's own static-hosting source — e.g. the `pages/` or `public/` directory of
   that site's repository or its static-hosting bucket.
2. Commit/upload through **that site's** normal pages pipeline (its own CI,
   static-host sync, or manual upload). Each file is fully self-contained, so no
   bundling, asset copying, or dependency install is required.
3. Confirm the published URL renders (suggested path:
   `https://portwright.io/ai-it-alerts.html` or wherever the site routes static
   pages).
4. Update the page's published date and the "Last updated" line in the footer if
   the content changed.

## Editing notes

- Keep pages dependency-free and self-contained (inline CSS; vanilla JS only if
  truly needed).
- Keep security claims defensible: no fabricated CVEs, vendor statements, or
  statistics. Attribute generically ("security researchers reported...").
- Preserve the honesty caveat about Merkle integrity vs. injection sanitization —
  integrity is tamper-evidence, sanitization is what actually stops injection.
