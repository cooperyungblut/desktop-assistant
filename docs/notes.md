# Notes

Tell her to write something down and she appends it to a notes page you can
keep open on another monitor.

## What to say

| You say | She does |
| --- | --- |
| "Write that down: the safe code is 4471" | Appends `- [17:21] the safe code is 4471` to the current page |
| "The safe code is 4471, write that down" | Same thing |
| "Take a note, lever three then lever one" / "Make a note that ..." / "Note: ..." | Same thing |
| "Write that down" (nothing after it) | Right after she answers something, saves her answer in a few words ("vault code 6762"). Otherwise asks what to write; your next message is the note ("never mind" cancels) |
| "Write down that vault code" / "Write down the vault code from the notes" | She works out the note from her last answer and the notes page and reads back what she wrote. Notes you say outright, or anything with a number in it, are written word for word |
| "Start a new page called Escape room" | Creates `Escape room.md` and writes there from now on |
| "Open a page called Test" / "Open notes called Escape room" / "Open the notes page Test" / "Switch to the page called ..." | Same, or goes back to that page (and opens it) if it exists. Names match loosely, so "test monitor on" finds `testMonitorOn` |
| "Open Test2" / "Switch to Escape room" | Goes to that page, only if a page by that name already exists |
| "Open Test 4 and write down jelly beans" | Goes to that page, then writes the note there |
| "Open the latest notes" / "Pull up my last page" | Opens the page written most recently |
| "Open the one before that" / "Go back to the previous page" | Steps back one page at a time, by when each was last written |
| "Start a new page" | Same, named with the date and time |
| "Read me my notes" / "What did I write down?" | Reads the last five notes on the current page |
| "Where did you write that down?" | Names the current page and its file |
| "Rename these notes Haunted House" / "Call this page Lighthouse" | Renames the current page (file and heading) and reopens it. Notepad++ may ask about the old tab; close it |
| "Open my notes" | Opens the current page in your viewer (see below) |
| "What was the gate code?" / "Check the notes for the gate code" | Answers from the page in use (its last 30 notes), if one was used this session or written in the last 12 hours. If that page doesn't have it, she checks the 10 newest other pages for notes on the same thing. A question asking for a code, password, PIN, combination or number that matches a note skips the Wikipedia lookup |
| "Cross off the vault notes" / "Mark the vault code as used" / "Archive the gate code" | Strikes through (`~~...~~`) every note on the current page that mentions the vault. If the current page has none ("the other gate code", "archive that"), she works out which note you mean and can cross it off on any recent page, matched by its number. Nothing is deleted; crossed-off notes are left out when she reads or answers from notes |

The note is written exactly as it was heard, in code: the chat model never
sees or rewrites it. She reads it back ("Got it, I wrote down: ..."), so you
hear right away if Whisper misheard a number.

### In your own words

The phrases above are handled by the app directly and fast. Anything else goes to
her, and she can act on notes herself: "the vault code is 55 then 26", "yes,
update it", "let's use the escape room notes". She adds a hidden tag to her reply,
`[note: ...]`, `[cross off: ...]`, `[open page: ...]` or `[rename page: ...]`, the same way she adds
mood tags. The app carries it out after she finishes speaking. If it fails
(notes turned off, a file error), she says so instead.

## Where notes go

`notes.dir` in `config.yaml` (default `data/notes`, which git ignores). Each
page is a Markdown file:

```markdown
# Escape room

- [17:21] the safe code is 4471
- [17:24] blue key is in the attic
```

A page you open or start is used until she's restarted. If you take a note
without opening one, she starts her own page, `JuniperNotes_<date>_1.md`, and
keeps using it that session; the next session that day gets `_2`, and so on
(`notes.default_page` sets the name). "Read my notes" and "open my notes"
with no page chosen use the page written most recently. If you point `dir` at
your synced Google Drive folder, the notes show up on your phone too.

## Watching notes live in Notepad++

1. Install [Notepad++](https://notepad-plus-plus.org/) and set
   `open_with: "C:/Program Files/Notepad++/notepad++.exe"` under `notes:` in
   `config.yaml` (the 32-bit version installs to
   `C:/Program Files (x86)/Notepad++/notepad++.exe` instead). She then opens each new page in Notepad++ when she starts it,
   and "open my notes" opens the current one.
2. She starts Notepad++ with `-monitor`, which turns on View > Monitoring
   (tail -f) for the page, so new notes show up while you're in another
   window. The tab goes read-only, which is fine since she does the writing.
   If a page opens without it (for example one you opened yourself), click
   the eye icon. Set `open_args: []` to leave it off.
3. Drag the Notepad++ window to the side monitor.

Without Monitoring, Notepad++ only checks the file when you click into it, and
it asks "This file has been modified by another program. Reload?" unless
Settings > Preferences > MISC. > File Status Auto-detection has "Update
silently" ticked.

Opening a page brings Notepad++ to the front, which can pull you out of a
full-screen game. Start the page before you start playing, or leave
`open_with` empty and open the file yourself once.

`open_with: "default"` uses whatever app Windows opens `.md` files with
instead (plain Notepad does not reload changed files).
