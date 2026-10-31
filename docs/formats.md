# File formats (the contract)

This is the contract between the **brain** (Claude scheduled tasks that write
briefs into a Google Drive folder) and the **face** (this app). Both sides
depend on it, so changes are versioned through the `schema` field and must stay
backward compatible within a major version.

## Ground rules

1. **One writer per file.** Claude writes brief files and never edits them
   afterwards. The app writes escalation files and is the only one that updates
   them. That avoids Google Drive sync conflicts (`file (1).md`).
2. **Plain text only.** Every file is UTF-8 Markdown with a YAML front-matter
   block. The scheduled task must create a real `.md` file
   (`mimeType: text/markdown`), **not a Google Doc**. A Google Doc syncs to the
   PC as a `.gdoc` shortcut that has no readable content.
3. **The app never deletes anything** in the synced folder.
4. Times are ISO 8601 with a UTC offset, e.g. `2026-11-02T09:00:12-06:00`.

## Folder layout (inside the synced Drive folder)

```
Secretary/
├── briefs/        # Claude writes: one file per brief
├── escalations/   # the app writes: one file per question for Claude
└── jobs/          # the job-search task writes its shortlist here (the app ignores it)
```

Only `briefs/` and `escalations/` matter to the app. The morning brief task
reads `jobs/` and folds the shortlist into the morning brief.

## Brief file — `briefs/YYYY-MM-DD_<type>.md`

`<type>` is one of `morning`, `afternoon`, `evening`. Example:
`briefs/2026-11-02_morning.md`.

```markdown
---
schema: secretary-brief/1
id: 2026-11-02_morning
type: morning
date: 2026-11-02
generated_at: 2026-11-02T09:00:12-06:00
answers_escalations: [esc-20261101-161204]
---

# Display

## Email
- **Interview invite** from Dana Reyes (Contoso Data): 30-min phone screen,
  asks you to pick a slot Tue or Wed.

## Calendar
- 10:00–10:30 Coffee chat with Sam (Teams)

# Spoken

Good morning, Cooper! Big one first: Contoso Data wants a phone screen...
```

### Front matter

| Field                 | Required | Meaning |
|-----------------------|----------|---------|
| `schema`              | yes      | Always `secretary-brief/1` for this version. |
| `id`                  | yes      | Same as the file name without `.md`. |
| `type`                | yes      | `morning`, `afternoon` or `evening`. |
| `date`                | yes      | The day the brief is about (`YYYY-MM-DD`). |
| `generated_at`        | yes      | When the task wrote it. |
| `answers_escalations` | no       | Ids of the escalation files this brief answers. Default `[]`. |
| `sample`              | no       | `true` marks made-up data (development, tests, demos). The UI tags it SAMPLE. Real briefs omit it. |

### Body

The body has exactly two level-1 sections, in this order:

- `# Display`: Markdown shown on screen. Use `##` and deeper headings inside it,
  never `#`.
- `# Spoken`: the script the avatar reads aloud, **verbatim**. Plain sentences:
  no Markdown, no bullet points, no URLs, no emoji. Spell out anything awkward to
  say ("ten thirty", not "10:30–11:00"). Aim for 60–150 seconds of speech.

If a brief answers escalations, the answer goes in both sections, for example
under a `## Your questions` heading in Display.

## Escalation file — `escalations/esc-YYYYMMDD-HHMMSS.md`

Written by the app when you explicitly ask it to check with Claude.

```markdown
---
schema: secretary-escalation/1
id: esc-20261102-101503
created_at: 2026-11-02T10:15:03-06:00
status: open
about_brief: 2026-11-02_morning
answered_in: null
---

# Question

Did the Contoso recruiter say whether the phone screen is technical?

# Context

Last few turns of the conversation, for reference:

- **Cooper:** what time is the Contoso thing?
- **Juniper:** They haven't picked a time yet; they asked you to choose Tue or Wed.
```

| Field         | Meaning |
|---------------|---------|
| `status`      | `open` until a brief lists this id in `answers_escalations`, then `answered`. |
| `about_brief` | The brief the conversation was about (may be `null`). |
| `answered_in` | Filled in by the app with the answering brief's id. |

### Lifecycle

1. The app writes the file with `status: open`.
2. The next scheduled brief task reads every file in `escalations/` with
   `status: open`, answers each question in the brief, and lists their ids in
   `answers_escalations`. **The task does not edit escalation files.**
3. When the app picks up that brief, it rewrites each listed escalation with
   `status: answered` and `answered_in: <brief id>`.

If the app is off when the brief lands, step 3 happens at the next startup. If
Claude runs twice before the app catches up, it may answer a question twice.
That's harmless.

## How the app marks a brief as handled

The app does **not** write into `briefs/`. It keeps a small local state file
(`data/state.json` by default, git-ignored, outside the synced folder):

```json
{
  "handled_briefs": {"2026-11-02_morning": "2026-11-02T09:41:10-06:00"}
}
```

A brief is **handled** once its readout has started. Unhandled briefs show the
"brief ready" indicator. Only the newest unhandled brief is offered; older ones
can still be opened from the history list.

## Instructions to paste into the scheduled tasks

Add this to each brief task's prompt (adjust the folder name):

> Write the brief as a plain Markdown file (mimeType text/markdown, not a Google
> Doc) named `YYYY-MM-DD_<type>.md` in the `Secretary/briefs` folder, following
> the format in the example exactly: YAML front matter with schema, id, type,
> date, generated_at and answers_escalations, then a `# Display` section and a
> `# Spoken` section. The Spoken section is read aloud verbatim by a cheerful
> assistant named Juniper: write it in her warm, upbeat voice, in plain spoken
> sentences, with no markdown, lists, links or emoji, and never soften or drop
> facts. Before writing, read every file in `Secretary/escalations` whose
> front matter says `status: open`, answer each question in both sections, and
> list their ids in answers_escalations. Do not modify or delete escalation files.
