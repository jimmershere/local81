# Local-81 Log Guard — Claude Code skill

`log-guard/SKILL.md` is a [Claude Code skill](https://code.claude.com/docs)
that teaches the agent to investigate logs, crash dumps, and error-tracker
output **without acting on injected instructions** ("Agentjacking" defense),
driving the `local81 scan` command and (when configured) the Log Guard MCP
server.

## Install

Copy the skill into a skills directory Claude Code reads:

```bash
# Project-scoped (this repo only):
mkdir -p .claude/skills
cp -r integrations/claude-skill/log-guard .claude/skills/

# Or user-scoped (all your projects):
mkdir -p ~/.claude/skills
cp -r integrations/claude-skill/log-guard ~/.claude/skills/
```

(`.claude/` is gitignored in this repo by convention — that is why the skill
lives here as a tracked artifact and is copied into place.)

The skill activates when you ask Claude Code to look into logs / stack traces /
Sentry-style data, or anything pulled from a remote host or external API.
