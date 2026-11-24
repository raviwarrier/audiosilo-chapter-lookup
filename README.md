# AudioSilo Chapter Lookup Sidecar

An independent Python CLI sidecar that finds and adds chapters to AudioSilo
audiobooks without running Whisper/Bridge transcription. Designed for
AudioSilo 2.6.0.

## The Problem

AudioSilo 2.6.0 only offers two ways to get chapters for a book:

1. **Community chapters** — If another user has already contributed chapters
   for that specific recording, they appear automatically. Most books don't
   have community chapters.
2. **Bridge transcription** — The Bridge runs Whisper speech recognition on
   the audio to detect chapter boundaries. This is resource-intensive and
   takes 20–45 minutes per book.

For books without community chapters, you're stuck waiting 20–45 minutes per
book for Bridge to transcribe the audio just to find chapter boundaries.

## What This Sidecar Does

Instead of running Whisper transcription, this sidecar looks up chapters from
**Audnexus** (a public chapter database) using the book's ASIN. It then writes
a `.audiosilo/chapters.json` sidecar file next to the audiobook and triggers
an AudioSilo rescan so the server ingests the chapters.

This is the third option: **external chapter lookup** — no transcription, no
Bridge, no 20–45 minute wait per book.

## How It Works

1. Find the book in your AudioSilo library by title/author
2. Look up the book's ASIN (if not already in the record, search the Audible
   catalogue)
3. Fetch chapters from Audnexus using the ASIN
4. Write `.audiosilo/chapters.json` next to the audio file with correct file
   size metadata
5. Trigger an AudioSilo library rescan via the admin API

## Steps to Configure, Install and Run

### 1. Configure

```powershell
copy .env.example .env
notepad .env
```

Key settings:

| Setting | Meaning |
|---|---|
| `AUDIOSILO_BASE_URL` | Your AudioSilo server URL |
| `AUDIOSILO_API_TOKEN` | Admin API token |
| `AUDIOSILO_LIBRARY_ID` | Your audiobook library ID |
| `AUDNEXUS_REGION` | Audnexus region (default `US`) |
| `DRY_RUN` | `true` = test only; `false` = update AudioSilo |

### 2. Install

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -e .
```

### 3. Run

**Auto mode (recommended):** Extracts books missing chapters from AudioSilo's
health API and processes them all:

```powershell
python process_book.py
```

This is equivalent to running:

```powershell
python extract_no_chapters.py
python process_book.py --file no_chapters.txt
```

**Single book:**

```powershell
python process_book.py --title "Book Title" --author "Author Name"
```

**Batch from an existing list file:**

```powershell
python process_book.py --file no_chapters.txt
# or equivalently: --batch-file no_chapters.txt
```

### 4. Review Results

After a batch run, check `unprocessed_titles.txt` for books that couldn't be
processed (no ASIN, no chapters on Audnexus, or rescan failure).

MIT License. Copyright (c) 2026.

## Vibe-Coding Disclaimer

This entire project was developed by AI coding agents with inputs from and testing by me. It made mistakes along the way and I tried to correct its course throughout the development. While I have thoroughly testing it on my server in my environment, I would urge you to look through the code (or ask your AI coding agent to do so) for validity and security issues.