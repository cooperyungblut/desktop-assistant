# Scheduled task prompts

What to add to each Claude scheduled task in claude.ai so its output matches
[the brief format](formats.md).

**What changes:** keep the part of each task's prompt that says *what to cover*
(which emails, which calendar range). Replace the part that says *how and where
to save the result* with the matching block below. Each block is self-contained:
it carries the exact file template, so the task doesn't need to see any other file.

After updating a task, use **Run now** once and check the Verify list at the bottom.

---

## Morning task (9:00 AM)

```text
OUTPUT FORMAT (strict, this file is read by a program):

Save the result as ONE plain-text Markdown file in the Google Drive folder
"Secretary/briefs". Create it as a plain text file with mimeType text/markdown.
Do NOT create a Google Doc. File name: YYYY-MM-DD_morning.md using today's date
(for example 2026-11-02_morning.md). If a file with that name exists, replace it.

Before writing:
1. Read the newest file in "Secretary/jobs" (today's job shortlist) and include it.
2. Read every file in "Secretary/escalations". For each one whose front matter
   says "status: open", answer the question under "# Question" as best you can
   from email and calendar. Do not modify or delete escalation files.

The file must look exactly like this template (replace the <...> parts):

---
schema: secretary-brief/1
id: <YYYY-MM-DD>_morning
type: morning
date: <YYYY-MM-DD>
generated_at: <current time, ISO 8601 with UTC offset, e.g. 2026-11-02T09:00:12-06:00>
answers_escalations: [<ids of the escalations you answered, comma separated, or leave empty>]
---

# Display

## Needs a reply
<bullets: sender, company, what they want, any deadline>

## FYI
<bullets>

## Calendar today
<table or bullets with times>

## Job shortlist
<numbered list: title, company, location, key skills>

## Your questions
<only if you answered escalations: quote each question, then the answer>

# Spoken

<The script read aloud by Juniper, a warm, upbeat assistant, to Cooper.
Plain spoken sentences only: no markdown, bullets, links, URLs or emoji.
Say times the way a person would ("ten thirty"). Lead with what matters most.
Never soften, drop or joke about facts: an interview invite stays an interview
invite, a rejection stays a rejection. 60 to 150 seconds when read aloud.
Answer any escalated questions here too.>

Keep it short (applies to both Display and Spoken):
- Job search: if no job-search mail came in (recruiters, interview invites,
  application updates, rejections), say only "No job search updates at the
  moment." Never list the kinds of mail that did not arrive.
- Anything else with nothing new: leave it out. Do not announce that it is empty.
- Automated emails (account confirmations, sign-in alerts, receipts, password
  resets, system notices): at most a few words each, saying what happened and
  nothing more. No explanations, no advice, no "if this wasn't you" warnings
  unless it looks like a real security problem. Group several into one line.
  In Spoken, skip them unless they need action, or cover them all in one short
  phrase.
- Real emails: one line each with who, what they want, and any deadline.

Rules: the "# Display" and "# Spoken" headings must appear exactly once each,
in that order. Inside Display, use "##" headings or deeper, never a single "#".
Leave out sections that would be empty, except Spoken, which is never empty.
```

## Afternoon task (1:30 PM)

Same block with three changes. Copy this version:

```text
OUTPUT FORMAT (strict, this file is read by a program):

Save the result as ONE plain-text Markdown file in the Google Drive folder
"Secretary/briefs". Create it as a plain text file with mimeType text/markdown.
Do NOT create a Google Doc. File name: YYYY-MM-DD_afternoon.md using today's
date. If a file with that name exists, replace it.

Before writing, read every file in "Secretary/escalations". For each one whose
front matter says "status: open", answer the question under "# Question" as
best you can from email and calendar. Do not modify or delete escalation files.

The file must look exactly like this template (replace the <...> parts):

---
schema: secretary-brief/1
id: <YYYY-MM-DD>_afternoon
type: afternoon
date: <YYYY-MM-DD>
generated_at: <current time, ISO 8601 with UTC offset, e.g. 2026-11-02T13:30:04-06:00>
answers_escalations: [<ids of the escalations you answered, comma separated, or leave empty>]
---

# Display

## New since this morning
<bullets: sender, company, what they want, any deadline>

## Your questions
<only if you answered escalations: quote each question, then the answer>

## Rest of today
<table or bullets with times>

# Spoken

<The script read aloud by Juniper, a warm, upbeat assistant, to Cooper.
Plain spoken sentences only: no markdown, bullets, links, URLs or emoji.
Say times the way a person would ("two thirty"). Lead with what matters most.
Never soften, drop or joke about facts. 30 to 90 seconds when read aloud.
Answer any escalated questions here too.>

Keep it short (applies to both Display and Spoken):
- Job search: if no job-search mail came in (recruiters, interview invites,
  application updates, rejections), say only "No job search updates at the
  moment." Never list the kinds of mail that did not arrive.
- Anything else with nothing new: leave it out. Do not announce that it is empty.
- Automated emails (account confirmations, sign-in alerts, receipts, password
  resets, system notices): at most a few words each, saying what happened and
  nothing more. No explanations, no advice, no "if this wasn't you" warnings
  unless it looks like a real security problem. Group several into one line.
  In Spoken, skip them unless they need action, or cover them all in one short
  phrase.
- Real emails: one line each with who, what they want, and any deadline.

Rules: the "# Display" and "# Spoken" headings must appear exactly once each,
in that order. Inside Display, use "##" headings or deeper, never a single "#".
Leave out sections that would be empty, except Spoken, which is never empty.
```

## Evening task (6:00 PM)

```text
OUTPUT FORMAT (strict, this file is read by a program):

Save the result as ONE plain-text Markdown file in the Google Drive folder
"Secretary/briefs". Create it as a plain text file with mimeType text/markdown.
Do NOT create a Google Doc. File name: YYYY-MM-DD_evening.md using today's date.
If a file with that name exists, replace it.

Before writing, read every file in "Secretary/escalations". For each one whose
front matter says "status: open", answer the question under "# Question" as
best you can from email and calendar. Do not modify or delete escalation files.

The file must look exactly like this template (replace the <...> parts):

---
schema: secretary-brief/1
id: <YYYY-MM-DD>_evening
type: evening
date: <YYYY-MM-DD>
generated_at: <current time, ISO 8601 with UTC offset, e.g. 2026-11-02T18:00:41-06:00>
answers_escalations: [<ids of the escalations you answered, comma separated, or leave empty>]
---

# Display

## Still needs a reply
<bullets: sender, company, what they want, any deadline>

## Your questions
<only if you answered escalations: quote each question, then the answer>

## Tomorrow
<table or bullets with tomorrow's calendar>

# Spoken

<The script read aloud by Juniper, a warm, upbeat assistant, to Cooper.
Plain spoken sentences only: no markdown, bullets, links, URLs or emoji.
Say times the way a person would ("nine thirty"). Lead with what matters most.
Never soften, drop or joke about facts. 30 to 90 seconds when read aloud.
Answer any escalated questions here too.>

Keep it short (applies to both Display and Spoken):
- Job search: if no job-search mail came in (recruiters, interview invites,
  application updates, rejections), say only "No job search updates at the
  moment." Never list the kinds of mail that did not arrive.
- Anything else with nothing new: leave it out. Do not announce that it is empty.
- Automated emails (account confirmations, sign-in alerts, receipts, password
  resets, system notices): at most a few words each, saying what happened and
  nothing more. No explanations, no advice, no "if this wasn't you" warnings
  unless it looks like a real security problem. Group several into one line.
  In Spoken, skip them unless they need action, or cover them all in one short
  phrase.
- Real emails: one line each with who, what they want, and any deadline.

Rules: the "# Display" and "# Spoken" headings must appear exactly once each,
in that order. Inside Display, use "##" headings or deeper, never a single "#".
Leave out sections that would be empty, except Spoken, which is never empty.
```

## Job search task

Only the save location changes, so the morning task can find the shortlist:

```text
Save the shortlist as a plain-text Markdown file (mimeType text/markdown, not a
Google Doc) in the Google Drive folder "Secretary/jobs", named
YYYY-MM-DD_shortlist.md using today's date.
```

The job search task must run **before** 9:00 AM so the morning brief can include it.

---

## Verify after "Run now"

1. In File Explorer, `G:\My Drive\Secretary\briefs` has a file ending in
   **`.md`**, not `.gdoc`. If you see `.gdoc`, the task created a Google Doc.
   Tell me, because the fix differs depending on what the Drive connector allows.
2. With the app running, the brief-ready button appears within about 5 seconds
   of the file showing up in Explorer. It has **no** SAMPLE tag.
3. If the button doesn't appear, the app's terminal prints a line like
   `Skipping 2026-11-02_morning.md for now: ...` that names the problem.
   Paste it to me.
