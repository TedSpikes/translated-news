---
name: design-log
description: Search and maintain the design-log/ folder, this project's history of design and engineering decisions. Use before starting work on a new feature or change, before writing a plan, and after finishing a change (to log it).
---

# Design Log

`design-log/` at the repo root holds a chronological history of design and
engineering decisions for this project: why things are built the way they
are, what alternatives were considered, and what tradeoffs were made. Treat
it as first-class project context, alongside the code itself.

## Before starting work (new feature, change, or plan)

Before writing a plan or making a non-trivial change, check `design-log/`
for relevant prior context.

- **Do not read every file.** That defeats the purpose and blows up context
  for no benefit on a log that will keep growing.
- List the folder first (`ls design-log/`) to see what dates/entries exist.
- Use `grep -l` / `grep -ril` over `design-log/` with keywords related to the
  feature or area you're touching to find candidate files cheaply.
- Only fully read the files that actually look relevant (matched keywords,
  or filenames/dates that line up with the area of work).
- Fold anything relevant you find into your plan or approach — don't just
  silently note it.

If nothing relevant turns up, say so briefly and proceed; don't force a
connection that isn't there.

## After finishing work

Once a change is done, record it in `design-log/`:

- **If the change extends work already logged** (you find an existing entry
  for the same feature/change from this session or a prior one), update
  that existing file rather than creating a new one.
- **If it's a new/distinct change**, create a new file.

### File naming

`<ISO-date>-<sequence>.md`, e.g. `2026-07-26-01.md`. The sequence number is
a two-digit counter starting at `01`, incrementing per additional entry
created on the same date. Check existing files for that date before picking
the next number.

### What to write

Keep it focused on decisions and reasoning, not a changelog of diffs (git
history already covers that). A good entry includes:

- What the change/feature is, briefly.
- Why it was needed (the problem or motivation).
- Key decisions made and alternatives considered/rejected, with reasoning.
- Any non-obvious constraints, tradeoffs, or follow-up considerations.

Skip entries for trivial changes (typo fixes, formatting, dependency bumps)
where there's no real decision to record.
