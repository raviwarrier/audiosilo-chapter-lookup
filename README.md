# AudioSilo Chapter Lookup Sidecar

An independent Python CLI sidecar that finds and adds chapters to AudioSilo
audiobooks without running Whisper/Bridge transcription. Designed for
AudioSilo 2.6.0.

## Limitations (as of AudioSilo 2.6.0)

The AudioSilo server does **not** expose a verified chapter-write endpoint in
2.6.0. This sidecar therefore works around the limitation in two ways:

1. **chapters.json sidecar + rescan trigger** — Writes
   `.audiosilo/chapters.json` next to the audio file using the schema AudioSilo
   expects (`version`, `files[]` with real `size`, `chapters[]` with
   `title`/`start_ms`/`length_ms`), then calls the admin rescan endpoint so the
   server ingests the sidecar. This is the default and recommended path.

2. **Direct database write** — The `direct_write` module exists for testing
   only (writes directly to AudioSilo's SQLite database). It is disabled by
   default (`DIRECT_WRITE_ENABLED=false`) and should not be used in production.

The `PUT /book/chapters` API endpoint referenced in the spec exists only in
unreleased main. Until it ships, the sidecar flow above is the working method.

## Why this was necessary

AudioSilo's server/bridge does not scan for or ingest user-supplied
`chapters.json` files automatically. The bridge's chapter detection is
transcription-based (Whisper), which this sidecar deliberately avoids per the
safety rules. The only reliable path to get chapters into AudioSilo 2.6.0
without transcription is to write the `chapters.json` sidecar with correct
file metadata (especially `files[].size`) and trigger a rescan.

## ASIN lookup from Audible

Some books in your library may not have an ASIN in their AudioSilo record.
In that case, `process_book.py` automatically queries the
[Audible catalogue API](https://api.audible.com/1.0/catalog/products)
by title and author to find a matching ASIN. If found, it proceeds with the
chapter lookup from Audnexus using that ASIN. If no ASIN is found, the book is
skipped and recorded in `unprocessed_titles.txt` with status `no_asin`.

## Steps to configure, install and run

### 1. Configure

```powershell
# Copy the example env and fill in your values
copy .env.example .env
# Edit .env with your AudioSilo API token and library ID
notepad .env
```

Key settings in `.env`:

| Setting | Meaning |
|---|---|
| `AUDIOSILO_BASE_URL` | Your AudioSilo server URL |
| `AUDIOSILO_API_TOKEN` | Admin API token |
| `AUDIOSILO_LIBRARY_ID` | Your audiobook library ID |
| `AUDNEXUS_REGION` | Audnexus region (default `US`) |
| `DRY_RUN` | `true` = dry-run only; `false` = update AudioSilo |

### 2. Install

```powershell
# Create and activate a virtual environment
python -m venv venv
venv\Scripts\Activate.ps1

# Install dependencies
pip install -e .
```

### 3. Process books (auto-extract + batch)

The simplest way: run with no arguments. This extracts a fresh `no_chapters`
list from AudioSilo's health API and batch-processes every book in it:

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

## Vibe-coded disclaimer

This project was developed with AI assistance (Claude Code). While the core
logic has been tested against a live AudioSilo 2.6.0 instance, AudioSilo API
endpoints and field names are candidate implementations that must be verified
against your running server. Always back up your AudioSilo database before
running batch writes.

## License

MIT License — Copyright (c) 2026. Permission is hereby granted, free of
charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to
the following conditions: the above copyright notice and this permission
notice shall be included in all copies or substantial portions of the Software.