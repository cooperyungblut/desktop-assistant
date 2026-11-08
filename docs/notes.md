# Notes

Tell her to write something down and she appends it to a notes page you can
keep open on another monitor.

## What to say

| You say | She does |
| --- | --- |
| "Write that down: the safe code is 4471" | Appends `- [17:21] the safe code is 4471` to the current page |
| "The safe code is 4471, write that down" | Same thing |
| "Take a note, lever three then lever one" / "Make a note that ..." / "Note: ..." | Same thing |
| "Write that down" (nothing after it) | Asks what to write; your next message is the note ("never mind" cancels) |
| "Start a new page called Escape room" | Creates `Escape room.md` and writes there from now on |
| "Open a page called Test" / "Open notes called Escape room" / "Open the notes page Test" / "Switch to the page called ..." | Same, or goes back to that page (and opens it) if it exists. Names match loosely, so "test monitor on" finds `testMonitorOn` |
| "Open the latest notes" / "Pull up my last page" | Opens the page written most recently |
| "Start a new page" | Same, named with the date and time |
| "Read me my notes" / "What did I write down?" | Reads the last five notes on the current page |
| "Where did you write that down?" | Names the current page and its file |
| "Open my notes" | Opens the current page in your viewer (see below) |

The note is written exactly as it was heard, in code: the chat model never
sees or rewrites it. She reads it back ("Got it, I wrote down: ..."), so you
hear right away if Whisper misheard a number.

## Where notes go

`notes.dir` in `config.yaml` (default `data/notes`, which git ignores). Each
page is a Markdown file:

```markdown
# Escape room

- [17:21] the safe code is 4471
- [17:24] blue key is in the attic
```

The current page is the one written most recently, so it carries over after a
restart. If you point `dir` at your synced Google Drive folder, the notes show
up on your phone too.

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
