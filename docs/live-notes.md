# Live notes

Live notes write a word-for-word transcript of a call, an interview or a video
to a notes page while it happens, so you can keep the page open on another
monitor (Notepad++ shows it live, see [notes.md](notes.md)):

```markdown
# Live notes 2026-11-15 1402

- [14:02] **Computer:** So tell me about a project you're proud of.
- [14:02] **You:** Sure. At my last job I rebuilt the outage map for the field crews.
- [14:03] **Computer:** What was the hardest part?
```

"You" is your microphone. "Computer" is whatever the computer plays (WASAPI
loopback on Windows), which is the other side of a Teams, Zoom or Meet call.
Telling apart several people on the computer side ("Voice 1", "Voice 2") is
a later step.

The transcript is Whisper's text exactly as heard. The chat model never sees
or rewrites these lines.

**Ask first.** Recording or transcribing a conversation can need everyone's
consent where you live, and it's the polite thing anyway. Tell the other
people that you're taking a transcript before you start.

## Starting and stopping

| You say / do | She does |
| --- | --- |
| "Start live notes" / "Transcribe this call" / "Start transcribing" / "Take notes of this meeting" | Starts on a new page, `Live notes <date> <time>`, opens it in your viewer and says: "Okay, I'm taking live notes on ... Make sure everyone knows the call is being transcribed." |
| "Start live notes for the Acme interview" | Same, on a page called `Acme interview` |
| "Start live notes hidden" / "... quietly" / "... in the background" | Starts without a word, hides her window, doesn't open the page, and shows the reminder as a Windows notification instead. Her window comes back when live notes stop |
| "Stop live notes" / "Stop transcribing" / "Stop listening" | Writes the last lines and says how many are on the page |
| Ctrl+Alt+L, or the tray menu | Start / stop (the tray also has "Start live notes, hidden") |
| The "live" dot on the full page | Click to start / stop; it's red while she's transcribing |
| Anything else ("my call's starting, keep track of what they say") | She works it out and starts or stops them herself (`[live notes: start]` tag) |

While live notes run, their page is the page in use: "what did he just ask?"
is answered from the transcript, and "write that down: ..." adds your own note
between the transcript lines. Her own voice is left out of the transcript (the
page tells the app when she's speaking).

## Her summary

Next to the transcript she keeps a short summary page, `<page> summary`, in
her own words: the topics, the questions they asked, what you said, and any
follow-ups (with names, dates and numbers as said). She rewrites it every
`summary_every_seconds` (2 minutes) while live notes run, and once more after
they stop ("My summary will be on ... in a moment"). Each update sends the chat
model only her summary so far and the transcript since the last update, so a
long interview never outgrows its context, and the model that's already loaded
does it (no extra VRAM).

While live notes run she also sees this summary, so "what have we covered so
far?" works. The transcript goes to the model as quoted data: anything someone
says on the call that sounds like an instruction to her is ignored.

## Setup

```powershell
uv sync --extra tts --extra stt --extra stt-gpu --extra wiki --extra live
uv run python scripts/live_notes_check.py --devices
```

The second line lists your microphones and speakers. Live notes use the
Windows defaults unless you set `live_notes.microphone_device` or
`speaker_device` in `config.yaml` (part of the name is enough).

## Try it without the app

```powershell
uv run python scripts/live_notes_check.py --seconds 60
```

It listens for a minute, prints each line as it's written, and saves them to
a "Live notes check ..." page in your notes folder. Play a YouTube video and
talk over it to hear both sides. `--no-computer` and `--no-mic` listen to one
side only.

## How it works

1. A thread per source reads 16 kHz audio (the `soundcard` package).
2. Each source is cut into chunks at pauses (`pause_seconds`, 0.7 s by
   default), or at a quiet moment if someone talks for more than
   `max_chunk_seconds`. The loudness that counts as speech follows the
   room's background noise, so a fan doesn't keep a chunk open.
3. Whisper (the same model as push-to-talk, so no extra VRAM) transcribes each
   chunk, given the last few sentences from that side as context. Segments
   Whisper itself rates as "no speech" are left out.
4. Lines wait `hold_seconds` (2.5 s) before they're written, so both sides go
   in time order and one person's back-to-back phrases join into one line.
5. With speakers instead of headphones the microphone hears the call too.
   `echo_check` leaves out a "You" line that repeats a "Computer" line from
   the same moment. Headphones avoid the problem entirely.

Audio is never written to disk. While listening, the last
`recent_seconds` (15 s) of each side stay in memory only, for a later
"write that down" with nothing after it; they're dropped when listening stops.

## Settings (`live_notes` in config.yaml)

| Setting | Default | What it does |
| --- | --- | --- |
| `microphone`, `computer_audio` | true | Which sides to listen to |
| `microphone_device`, `speaker_device` | unset | Part of a device name; Windows default if unset |
| `you_label`, `computer_label` | You, Computer | The names on each line |
| `page` | `Live notes {date} {time}` | The page each session writes to |
| `pause_seconds` | 0.7 | Quiet that ends a phrase |
| `max_chunk_seconds` | 12 | Longest chunk before a cut at a quiet moment |
| `min_level` | 0.006 | Quietest level that can count as speech (0 to 1) |
| `echo_check` | true | Skip "You" lines that repeat the call |
| `hold_seconds` | 2.5 | How long a line waits before it's written |
| `recent_seconds` | 15 | Audio kept in memory per side while listening |
| `summary` | true | Keep her summary page |
| `summary_every_seconds` | 120 | How often she rewrites it while listening |
| `summary_words` | 250 | About how long it may get |
