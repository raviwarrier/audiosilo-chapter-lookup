# AudioSilo Chapter Sidecar

## Requirements, architecture and implementation brief

**Document status:** Implementation specification / discovery brief\
**Target AudioSilo version:** 2.6.0\
**Purpose:** Give a coding agent enough context to investigate, design,
implement and test an independent Python sidecar that finds and adds
chapters to AudioSilo books without running speech transcription.

------------------------------------------------------------------------

## 1. Problem and objective

The AudioSilo library has approximately 849 books listed in its **Long
books without chapters** health-check category.

AudioSilo can generate chapters by transcribing audiobook audio with
Whisper and analysing the transcription. That approach may produce
useful results, but it is expensive in time and compute: one book can
take approximately 30--45 minutes and consume GPU resources.

The preferred workflow is faster, external chapter lookup, similar in
principle to Audiobookshelf's chapter lookup. Chapter titles may not
always be ideal, but getting usable chapter boundaries quickly is more
valuable than waiting for transcription.

Build an independent Python sidecar that:

1.  Accepts one book (by title, preferably with optional author) or a
    batch of books.
2.  Checks AudioSilo to determine whether the book already has usable
    chapters.
3.  If not, checks AudioSilo's community chapter mechanism.
4.  If community chapters are not available, performs an
    Audiobookshelf-style external lookup.
5.  Validates and converts returned chapters to AudioSilo's required
    representation.
6.  Saves chapters through a supported AudioSilo API, if available in
    the installed version.
7.  Records the outcome as complete, unavailable, or retryable failure.

**The sidecar must not invoke Whisper, Bridge transcription, or
AudioSilo's narrative chapter-generation pipeline.**

## 2. Known environment and facts

The following information was supplied by the owner and should be
treated as the current environment baseline:

  Item                         Known value
  ---------------------------- --------------------------------------------
  AudioSilo version            `2.6.0`
  API version                  `v1`
  `metadata` capability        `true`
  `meta_bundle` capability     `true`
  `bridge` capability          `true`
  AudioSilo server             `http://192.168.68.102:8080`
  Library health category      `Long books without chapters`
  Approximate affected books   `849`
  Preferred method             External chapter lookup, not transcription
  Preferred implementation     Independent Python sidecar

The capability flags indicate that metadata and Bridge-related features
are advertised by the server. They do **not**, by themselves, prove that
community chapter lookup is configured correctly, that every relevant
endpoint exists in 2.6.0, or that the community service has a match for
a given book.

The owner has not yet supplied: - The audiobook library ID. - A
representative book path and its admin API response. - The exact API
authentication method and token handling to use. - A verified response
schema for the community chapter check. - A verified request schema for
writing chapters.

These are discovery tasks, not assumptions to code around.

## 3. Desired user experience

The utility should be a command-line program. It should support:

``` text
chapter_sidecar list
chapter_sidecar show --title "Book title"
chapter_sidecar process --title "Book title"
chapter_sidecar process --title "Book title" --author "Author name"
chapter_sidecar process --batch 1
chapter_sidecar process --all
chapter_sidecar retry
chapter_sidecar report
```

Recommended additional options:

``` text
--dry-run
--limit 10
--force
--provider audnexus
--library-id <id>
--verbose
```

Do not implement every option blindly. Keep the first release small, but
design the command structure so these options can be added without
rewriting the processing logic.

### Expected behaviour

-   `list` displays eligible books and their AudioSilo identifiers.
-   `show` displays the selected book's metadata, chapter status and
    lookup identifiers without changing anything.
-   `process --title` processes exactly one unambiguous book. If
    multiple books match, show the candidates and require a more
    specific selection.
-   `process --batch N` processes a stable, reproducible batch. Define
    how batches are numbered and persist or deterministically derive the
    ordering.
-   `process --all` processes all eligible books, with conservative
    concurrency and rate limiting.
-   `retry` retries transient failures and optionally rechecks
    unavailable books when explicitly requested.
-   `report` summarises counts by status and provider.

A dry run must perform discovery and lookup but must not write chapters
or mark a book complete.

## 4. Processing workflow

For each selected book, use this order:

1.  Fetch the current AudioSilo book record.
2.  Determine whether it already has usable chapters.
3.  If it does, record `already_has_chapters` and stop unless an
    explicit, carefully controlled force mode is requested.
4.  Check whether AudioSilo community chapters already exist or can be
    obtained through its supported community-check mechanism.
5.  Re-fetch the book after the asynchronous community check and
    determine whether usable community chapters are now available.
6.  If community lookup did not provide usable chapters, search an
    external chapter source.
7.  Match the correct audiobook recording, not merely a book with a
    similar title.
8.  Retrieve the chapter list for the selected recording.
9.  Convert and validate the chapter list against the actual AudioSilo
    audiobook duration and file structure.
10. In dry-run mode, display the proposed result and stop.
11. Save through AudioSilo's supported chapter-writing mechanism, if
    verified for 2.6.0.
12. Re-fetch the book and verify that AudioSilo now exposes the expected
    chapters.
13. Mark the sidecar record complete only after the post-write
    verification succeeds.

### Important distinction: no chapters vs. no community result

The Health page's `Long books without chapters` category must not be
treated as proof that the community lookup has already been attempted or
failed.

The sidecar must separately determine: - Whether the book currently has
effective chapters. - Whether community chapters exist. - Whether a
community check is running. - Whether a community check has been
attempted. - Whether it found a recording. - Whether it found chapter
data. - Whether the chapter data could be aligned or accepted.

Use the actual 2.6.0 API response and server behaviour to identify these
fields. Do not infer their names from this document.

## 5. Lookup strategy

### 5.1 AudioSilo community lookup

The intended order is to use AudioSilo's own community chapter mechanism
before an external fallback.

Prior investigation identified the following endpoint as a candidate:

``` http
POST /api/v1/admin/libraries/{library_id}/book/community-chapters?path={book_path}
```

This endpoint and its exact parameters, authentication requirements,
response body and asynchronous completion behaviour **must be verified
against the running 2.6.0 instance** before implementation.

If the operation is asynchronous: - Start the check once. - Poll the
documented book/admin endpoint at a sensible interval. - Apply a
timeout. - Stop polling when the check is no longer running. - Re-fetch
the effective book/chapter data. - Do not repeatedly start the same
check while it is already running.

Do not call the endpoint if the book already has usable chapters or a
community check is already running.

### 5.2 External lookup: ABS-style fallback

The desired fallback is a recording-specific lookup, not a generic web
search.

A candidate lookup path identified during preliminary research is:

1.  Search AudioSilo Meta's Audiobookshelf-compatible search endpoint:

    ``` text
    https://meta.audiosilo.app/abs/search
    ```

    Candidate search inputs include title, author and ISBN.

2.  Use the returned recording identifier/ASIN to request chapter data
    from Audnexus:

    ``` text
    https://api.audnex.us/books/{ASIN}/chapters?region=in
    ```

These URLs and their current response schemas, availability, terms, rate
limits and region behaviour must be independently verified before coding
against them. Treat them as candidate services, not guaranteed
contracts.

The lookup adapter should be provider-independent so another source can
be added later.

#### Search identifiers in priority order

Use identifiers available in the AudioSilo record:

1.  ASIN, if the provider supports direct lookup.
2.  ISBN, where supported.
3.  Exact title + author.
4.  Normalised title + author as a fallback.

Do not accept a title-only result automatically when several recordings
or editions are plausible.

#### Recording match validation

A matching title does not prove that the chapter list belongs to the
correct recording. Compare as many of these as are available:

-   Title.
-   Author / narrator, where available.
-   ASIN or ISBN.
-   Edition / publisher.
-   Language.
-   Recording duration.
-   Number of audio files and total duration.

Prefer exact identifier matches. For title-based matches, use a
conservative score and reject ambiguous candidates rather than silently
choosing the first result.

Duration comparison needs a configurable tolerance. Do not assume all
editions have identical durations. The match threshold should be tested
on real library examples and documented.

### 5.2.1 What Audiobookshelf actually does

This section records the implementation found in the Audiobookshelf
source code, rather than relying only on its README description. The
source paths below were inspected on the `master` branch at commit
`5de038dafda294d13ca48861eda7d4405bbd7368`. Re-check them if the
implementation is based on a later Audiobookshelf release.

**Important:** Audiobookshelf's chapter lookup is a metadata lookup. It
does not listen to the audiobook, transcribe it, detect pauses, or
calculate chapter boundaries from the audio. It retrieves chapter
timings already associated with an Audible recording through Audnexus.

#### A. The user supplies or confirms the Audible ASIN and region

In `client/pages/audiobook/_id/chapters.vue`:

- The ASIN input is initially populated from
  `libraryItem.media.metadata.asin`.
- The user can edit the ASIN before requesting chapters.
- The Audible region is selectable. The UI initially uses `US`, and
  remembers the user's last selected region in browser `localStorage`
  under `audibleRegion`.
- The lookup is not based on title search at this point. It requires an
  ASIN. If the field is empty, the UI reports that an ASIN is required.

This is a key design point for the sidecar: **finding a plausible book
record and finding the correct Audible recording are separate tasks.**
The sidecar must resolve and validate the recording ASIN before asking
Audnexus for chapters. It should not assume that a generic metadata
search result automatically contains the correct Audible ASIN.

#### B. Audiobookshelf's backend calls Audnexus directly

The relevant backend files are:

- `server/controllers/SearchController.js`
- `server/finders/BookFinder.js`
- `server/providers/Audnexus.js`

The request path is:

```text
GET /api/search/chapters?asin={ASIN}&region={REGION}
```

`SearchController.findChapters()`:

1. Reads the `asin` query parameter. It is required.
2. Reads `region`; the backend default is `us`.
3. Converts the region to lowercase.
4. Validates the ASIN with Audiobookshelf's `isValidASIN()` utility.
5. Calls `BookFinder.findChapters(asin, region)`.
6. Returns the Audnexus response unchanged when found.
7. Returns a structured `Chapters not found` response when the provider
   returns no chapter data.

`BookFinder.findChapters()` is a thin delegation to
`Audnexus.getChaptersByASIN()`.

`Audnexus.getChaptersByASIN()` constructs this request:

```text
GET https://api.audnex.us/books/{URL-encoded-uppercase-ASIN}/chapters?region={region}
```

The region query parameter is omitted only when no region is supplied.
Audiobookshelf passes the returned JSON data through; it does not
rebuild the Audnexus chapter response on the server.

The provider also applies request throttling (one request every 150 ms
in the inspected source). It retries HTTP 429 responses using the
`Retry-After` response header, with a five-second fallback when the
header is absent. Other provider errors are logged and ultimately
returned to the caller as no chapter result. The sidecar should use
more explicit error classification in its own logs and state database,
so a provider outage is not confused with a genuine absence of chapters.

#### C. The client converts Audnexus milliseconds into ABS chapter times

The Audnexus response contains chapter entries with fields used by the
client:

- `title`
- `startOffsetMs`
- `startOffsetSec`
- `lengthMs`

In `applyChapterData()` in `chapters.vue`, Audiobookshelf:

1. Filters out chapters whose `startOffsetSec` is not less than the
   audiobook's media duration.
2. Converts `startOffsetMs` to seconds by dividing by `1000`.
3. Calculates each end time as
   `(startOffsetMs + lengthMs) / 1000`.
4. Caps the end time at the audiobook's media duration.
5. Assigns sequential chapter IDs.
6. Merges the fetched chapters into the editor's existing chapter list.
   Chapters that the user has locked are retained; unlocked positions
   are filled from the Audnexus list.

The client does not perform audio fingerprinting or timestamp
alignment. It trusts the Audnexus offsets, subject to the duration
filter and end-time cap.

For the sidecar, the corresponding conversion is:

```python
start_seconds = chapter["startOffsetMs"] / 1000.0
end_seconds = min(
    media_duration_seconds,
    (chapter["startOffsetMs"] + chapter["lengthMs"]) / 1000.0,
)
```

Do not silently copy `startOffsetSec` into AudioSilo. The values sent
to Audiobookshelf's chapter-save endpoint are seconds, while the
provider's offset and length fields are milliseconds.

The sidecar should additionally validate ordering, duplicate starts,
negative values, empty titles, and whether the final chapter boundaries
fit the actual AudioSilo duration. These extra checks are sensible
sidecar safeguards; they are not claims about Audiobookshelf's own
validation.

#### D. Audiobookshelf saves chapters through its normal item API

The client posts the edited chapter list to:

```text
POST /api/items/{library_item_id}/chapters
Content-Type: application/json

{
  "chapters": [
    {
      "id": 0,
      "start": 0.0,
      "end": 612.5,
      "title": "Chapter 1"
    }
  ]
}
```

The server route is registered in `server/routers/ApiRouter.js` and
handled by `LibraryItemController.updateMediaChapters()`.

The server:

- Requires the authenticated user to have `canUpdate` permission.
- Rejects missing items, non-book items, and items without audio tracks.
- Requires `chapters` to be an array.
- Requires each supplied chapter to have a non-empty string `title`
  and numeric `start` and `end` values.
- Rebuilds or updates the chapter entries with sequential IDs and the
  `title`, `start`, and `end` fields.
- Saves the media record and calls `saveMetadataFile()` when something
  changed.
- Emits an `item_updated` event.
- Returns `success` and `updated` fields.

The client sets each chapter's end time to the next chapter's start
time, and the final chapter's end time to the media duration, before
saving. The endpoint accepts an empty array too; the UI uses this to
remove all chapters.

This is the important architectural lesson for the sidecar: Audiobookshelf
does not write directly to its database from the chapter editor. It
uses the application's authenticated item API, which updates the media
record and metadata file through the application's own model logic.

**Do not copy Audiobookshelf's endpoint or payload into AudioSilo.**
AudioSilo has its own API and chapter representation. The sidecar should
use the ABS flow as a reference for provider lookup and the concept of
saving through the host application's supported API, then use the
verified AudioSilo 2.6.0 API for the actual write.

#### E. What to reproduce, adapt, and improve

| Area | Audiobookshelf behaviour | Sidecar requirement |
|---|---|---|
| Recording identity | User-provided or item metadata ASIN | Resolve ASIN from AudioSilo metadata/provider results; allow manual override |
| Region | User-selectable Audible region, remembered by browser | Configurable region; default to `IN` for this installation, but allow override |
| Chapter provider | Audnexus | Implement Audnexus as a provider adapter |
| Provider request | ASIN + region | Same endpoint pattern, after verifying current service behaviour |
| Rate limiting | One request per 150 ms | Conservative configurable rate limit; respect `Retry-After` |
| Timestamp conversion | Milliseconds to seconds | Convert explicitly and validate against AudioSilo duration |
| Alignment | No audio-based alignment in ABS lookup | Do not run transcription; optionally reject implausible duration mismatches |
| Editing | Existing chapters can be locked and merged in the UI | Initially avoid overwriting existing chapters; require explicit force/replace |
| Persistence | Authenticated item API updates model and metadata file | Use AudioSilo's verified chapter-writing API, never direct DB writes |

For this installation, `IN` is a sensible initial Audnexus region because
the library is being operated in India. However, do not assume that
every Audible title is available in the Indian catalogue. Make region
configurable and permit trying another region when the user or a
reliable match indicates that the ASIN belongs to another Audible
market.

#### F. Source files to consult during implementation

Use these files as the reference implementation:

- `server/providers/Audnexus.js` — Audnexus URL, region parameter,
  throttling and HTTP 429 retry.
- `server/finders/BookFinder.js` — delegation to the Audnexus provider.
- `server/controllers/SearchController.js` — ASIN/region validation and
  the `/api/search/chapters` response.
- `client/pages/audiobook/_id/chapters.vue` — ASIN/region UI, lookup,
  millisecond-to-second conversion, duration handling and save request.
- `server/routers/ApiRouter.js` — chapter-save route registration.
- `server/controllers/LibraryItemController.js` —
  `updateMediaChapters()` validation and persistence.

Pinned source links:

- [Audnexus provider](https://github.com/advplyr/audiobookshelf/blob/5de038dafda294d13ca48861eda7d4405bbd7368/server/providers/Audnexus.js)
- [BookFinder](https://github.com/advplyr/audiobookshelf/blob/5de038dafda294d13ca48861eda7d4405bbd7368/server/finders/BookFinder.js)
- [SearchController](https://github.com/advplyr/audiobookshelf/blob/5de038dafda294d13ca48861eda7d4405bbd7368/server/controllers/SearchController.js)
- [Chapter editor UI](https://github.com/advplyr/audiobookshelf/blob/5de038dafda294d13ca48861eda7d4405bbd7368/client/pages/audiobook/_id/chapters.vue)
- [ApiRouter](https://github.com/advplyr/audiobookshelf/blob/5de038dafda294d13ca48861eda7d4405bbd7368/server/routers/ApiRouter.js)
- [LibraryItemController](https://github.com/advplyr/audiobookshelf/blob/5de038dafda294d13ca48861eda7d4405bbd7368/server/controllers/LibraryItemController.js)

### 5.3 Provider result states

The provider adapter should distinguish:

-   No matching recording.
-   Recording found, but no chapter list.
-   Chapter list returned and valid.
-   Ambiguous recording match.
-   Rate limited.
-   Network timeout / provider unavailable.
-   Unexpected provider response.

A temporary provider failure must never be recorded as permanently
unavailable.

## 6. Chapter data normalisation and validation

Create one internal chapter representation independent of any provider
or AudioSilo schema:

``` python
from dataclasses import dataclass

@dataclass(frozen=True)
class Chapter:
    title: str
    start_seconds: float
    end_seconds: float
```

External providers may return milliseconds, offsets plus durations, or
other structures. Convert them in the provider adapter. The rest of the
program should use seconds consistently.

### Required validation

Before writing, validate:

-   At least one chapter exists.
-   Every chapter has a usable title. If a provider omits a title, use a
    conservative fallback such as `Chapter 1`; do not invent descriptive
    names.
-   Start and end values are numeric and finite.
-   Start is non-negative.
-   End is greater than start.
-   Chapters are ordered chronologically.
-   No chapter extends beyond the actual audiobook duration, except for
    a small configurable rounding tolerance.
-   There are no unintended overlaps or large unexplained gaps.
-   The first and last boundaries are plausible for the source format.
-   Chapter count is plausible for the duration.

Do not automatically stretch, shift or proportionally scale all chapter
timestamps to fit a different recording. That could make every boundary
wrong. If alignment is required, use AudioSilo's own documented
community alignment process or a separately tested alignment algorithm.

### Multi-file audiobooks

AudioSilo may represent a book as multiple audio files while presenting
a continuous book timeline. Verify whether the native chapter-writing
API expects: - A single continuous timeline across all files; or -
File-relative timestamps plus a file/track association.

Do not guess. Inspect the server's chapter model and API implementation,
and test with a multi-file book before enabling batch writes.

## 7. Writing chapters into AudioSilo

Preliminary research identified this as a candidate native endpoint:

``` http
PUT /api/v1/admin/libraries/{library_id}/book/chapters?path={book_path}
```

The exact endpoint, body schema, required `chapter_basis`, accepted
timestamp units, chapter source semantics and overwrite behaviour must
be verified against AudioSilo 2.6.0.

**Use the supported API, not direct SQLite writes**, unless
investigation proves that no supported write path exists and the owner
explicitly approves a database-level alternative.

The sidecar must: - Preserve existing chapters. - Avoid replacing
manually edited chapters. - Avoid replacing chapters generated by
AudioSilo unless explicitly authorised. - Use the exact chapter
basis/source value required by the installed server. - Verify the saved
chapters by fetching the book again. - Treat a successful HTTP response
as insufficient until the saved result is confirmed.

Preliminary documentation suggests that chapters written through the
admin chapter endpoint may be recorded as `edited`. If confirmed,
document this clearly. Store the true external provider and source
identifier in the sidecar's own database. Do not misrepresent externally
sourced chapters as AudioSilo community chapters.

### Source precedence

Before writing, inspect AudioSilo's chapter-source selection rules.
Determine how `files`, `community`, `generated` and `edited` sources
interact in 2.6.0.

The sidecar must not write chapters that are saved successfully but
hidden by AudioSilo's source-selection rules.

## 8. Sidecar state database

Use a separate SQLite database owned by the sidecar. Do not modify
AudioSilo's database.

Suggested table:

``` sql
CREATE TABLE IF NOT EXISTS book_jobs (
    audio_silo_library_id TEXT NOT NULL,
    book_path TEXT NOT NULL,
    book_id TEXT,
    title TEXT,
    author TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    community_status TEXT,
    external_provider TEXT,
    external_recording_id TEXT,
    chapter_count INTEGER,
    last_attempt_at TEXT,
    completed_at TEXT,
    retry_after TEXT,
    error_code TEXT,
    error_message TEXT,
    source_payload_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (audio_silo_library_id, book_path)
);
```

Use UTC ISO-8601 timestamps.

Suggested statuses:

  -----------------------------------------------------------------------
  Status                              Meaning
  ----------------------------------- -----------------------------------
  `pending`                           Not processed yet

  `already_has_chapters`              AudioSilo already had usable
                                      chapters

  `community_success`                 Community lookup supplied usable
                                      chapters

  `external_success`                  External lookup supplied and saved
                                      usable chapters

  `unavailable`                       All configured sources were checked
                                      successfully and no usable chapter
                                      list was found

  `lookup_failed`                     Temporary failure; eligible for
                                      retry

  `ambiguous_match`                   More than one plausible recording;
                                      needs review

  `write_failed`                      Lookup succeeded, but AudioSilo did
                                      not accept or expose the chapters

  `skipped`                           Explicitly excluded by the operator
  -----------------------------------------------------------------------

Do not mark a book `unavailable` because of a timeout, HTTP 429, 5xx
response, authentication failure or malformed response. Those are
failures, not negative lookup results.

A completed record should be revalidated against AudioSilo before being
skipped on a later run. If the book has changed or no longer exposes
chapters, make it eligible for investigation again.

## 9. Suggested project structure

``` text
audio-silo-chapter-sidecar/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── chapter_sidecar/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── audiosilo_client.py
│   ├── community.py
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── audiosilo_meta.py
│   │   └── audnexus.py
│   ├── matching.py
│   ├── chapters.py
│   ├── database.py
│   ├── processor.py
│   └── reporting.py
├── tests/
│   ├── fixtures/
│   ├── test_chapters.py
│   ├── test_matching.py
│   ├── test_providers.py
│   ├── test_processor.py
│   └── test_audiosilo_client.py
└── data/
    └── .gitkeep
```

Keep credentials in `.env` or environment variables. Do not commit
`.env`, API tokens, cookies or real library data.

## 10. Configuration

Example `.env.example`:

``` dotenv
AUDIOSILO_BASE_URL=http://192.168.68.102:8080
AUDIOSILO_API_TOKEN=
AUDIOSILO_LIBRARY_ID=

SIDECAR_DATABASE=./data/chapter_sidecar.sqlite3

AUDIOSILO_REQUEST_TIMEOUT=20
PROVIDER_REQUEST_TIMEOUT=20
COMMUNITY_POLL_INTERVAL=5
COMMUNITY_POLL_TIMEOUT=180

MAX_CONCURRENT_BOOKS=1
REQUEST_DELAY_SECONDS=1
DURATION_TOLERANCE_SECONDS=5
```

These are starting defaults, not confirmed requirements. The agent must
establish AudioSilo's actual authentication method and whether an API
key can be used for these admin endpoints.

Do not assume that the server accepts a bearer token until verified.
Support the authentication method documented by the installed API.

## 11. Suggested code skeletons for complex parts

The following snippets illustrate boundaries and safety rules. They are
not a complete implementation. The agent must adapt them to verified
AudioSilo 2.6.0 API schemas and actual provider responses.

### 11.1 Provider-independent interface

``` python
# providers/base.py

from dataclasses import dataclass
from typing import Protocol

@dataclass
class RecordingMatch:
    provider: str
    recording_id: str
    title: str
    author: str | None
    duration_seconds: float | None
    raw: dict

@dataclass
class Chapter:
    title: str
    start_seconds: float
    end_seconds: float

class ChapterProvider(Protocol):
    def search(
        self,
        *,
        title: str,
        author: str | None,
        isbn: str | None,
        asin: str | None,
    ) -> list[RecordingMatch]:
        ...

    def get_chapters(
        self,
        recording: RecordingMatch,
    ) -> list[Chapter] | None:
        ...
```

Provider-specific response parsing belongs inside each provider adapter.
Do not leak Audnexus or AudioSilo Meta response fields into the
processor.

### 11.2 Chapter conversion and validation

``` python
# chapters.py

import math
from dataclasses import dataclass

@dataclass(frozen=True)
class Chapter:
    title: str
    start_seconds: float
    end_seconds: float


def milliseconds_to_seconds(value: int | float) -> float:
    """Convert a provider timestamp from milliseconds to seconds."""
    result = float(value) / 1000.0
    if not math.isfinite(result):
        raise ValueError("Timestamp is not finite")
    return result


def chapters_from_offsets(items: list[dict]) -> list[Chapter]:
    """
    Example adapter for a provider returning startOffsetMs and
    durationMs. Confirm the real provider schema before using.
    """
    chapters = []

    for item in items:
        start = milliseconds_to_seconds(item["startOffsetMs"])
        duration = milliseconds_to_seconds(item["durationMs"])

        chapters.append(
            Chapter(
                title=(item.get("title") or "").strip(),
                start_seconds=start,
                end_seconds=start + duration,
            )
        )

    return chapters


def validate_chapters(
    chapters: list[Chapter],
    audiobook_duration: float,
    tolerance: float = 5.0,
) -> list[Chapter]:
    """Reject unsafe or implausible chapter timelines."""
    if not chapters:
        raise ValueError("Provider returned no chapters")

    if audiobook_duration <= 0:
        raise ValueError("Audiobook duration is invalid")

    ordered = sorted(chapters, key=lambda item: item.start_seconds)

    validated = []

    for index, chapter in enumerate(ordered):
        title = chapter.title or f"Chapter {index + 1}"

        if not math.isfinite(chapter.start_seconds):
            raise ValueError("Invalid chapter start")
        if not math.isfinite(chapter.end_seconds):
            raise ValueError("Invalid chapter end")
        if chapter.start_seconds < 0:
            raise ValueError("Negative chapter start")
        if chapter.end_seconds <= chapter.start_seconds:
            raise ValueError("Chapter end must follow its start")
        if chapter.end_seconds > audiobook_duration + tolerance:
            raise ValueError("Chapter exceeds audiobook duration")

        if validated:
            previous = validated[-1]
            if chapter.start_seconds < previous.end_seconds - 0.5:
                raise ValueError("Chapter overlap exceeds allowed tolerance")

        validated.append(
            Chapter(
                title=title,
                start_seconds=chapter.start_seconds,
                end_seconds=chapter.end_seconds,
            )
        )

    return validated
```

The example sorts chapters for validation. In production, preserve the
provider's original ordering in diagnostic output and report if sorting
was necessary. Do not silently conceal a badly ordered provider
response.

The overlap tolerance and duration tolerance must be configurable and
tested. Some legitimate formats may contain small gaps or overlaps.

### 11.3 Conservative recording selection

``` python
# matching.py

from difflib import SequenceMatcher

def normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().strip().split())


def similarity(left: str | None, right: str | None) -> float:
    return SequenceMatcher(
        None,
        normalise_text(left),
        normalise_text(right),
    ).ratio()


def rank_recordings(
    candidates,
    *,
    title: str,
    author: str | None,
    asin: str | None,
    isbn: str | None,
):
    """
    Rank candidates, but do not automatically accept a title-only match.
    Exact identifier matching should take precedence.
    """
    ranked = []

    for candidate in candidates:
        raw = candidate.raw

        candidate_asin = raw.get("asin")
        candidate_isbn = raw.get("isbn")

        if asin and candidate_asin == asin:
            ranked.append((1.0, candidate))
            continue

        if isbn and candidate_isbn == isbn:
            ranked.append((1.0, candidate))
            continue

        title_score = similarity(title, candidate.title)
        author_score = (
            similarity(author, candidate.author)
            if author and candidate.author
            else None
        )

        # A missing author is not treated as a perfect author match.
        score = (
            0.75 * title_score + 0.25 * author_score
            if author_score is not None
            else title_score * 0.75
        )

        ranked.append((score, candidate))

    return sorted(ranked, key=lambda item: item[0], reverse=True)
```

This is only a starting point. The agent must inspect actual search
result fields, identifiers and edition information. Implement explicit
acceptance and ambiguity thresholds, and test them against a
representative sample. Never use the first search result simply because
it is first.

### 11.4 Safe community-check polling

``` python
# community.py

import time

def wait_for_community_check(
    get_book,
    *,
    interval_seconds: int = 5,
    timeout_seconds: int = 180,
):
    """
    get_book must call the verified AudioSilo admin endpoint and
    return the current book response.
    """
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        book = get_book()

        # Replace this with the actual 2.6.0 response field.
        checking = book.get("community_checking")

        if checking is False:
            return book

        if checking is None:
            raise RuntimeError(
                "Community-check state field is missing; "
                "verify the installed API response schema"
            )

        time.sleep(interval_seconds)

    raise TimeoutError("Community chapter check did not finish in time")
```

Do not start another community check merely because polling timed out.
First inspect the current server state.

### 11.5 Processing orchestration

``` python
# processor.py

def process_book(book, *, audiosilo, community, providers, store, dry_run=False):
    """
    High-level orchestration. API-specific details belong in the
    client and provider adapters.
    """

    current = audiosilo.get_book(book.path)

    if audiosilo.has_usable_chapters(current):
        store.set_status(book, "already_has_chapters")
        return "already_has_chapters"

    # Check or run AudioSilo community lookup first.
    community_result = community.obtain_chapters(book, current)

    if community_result and community_result.chapters:
        chapters = audiosilo.validate_chapters_for_book(
            book,
            community_result.chapters,
        )

        if dry_run:
            return "dry_run_community_success"

        # Community chapters should normally be exposed by AudioSilo
        # itself. Do not copy them into the edited source without a
        # deliberate, documented reason.
        verified = audiosilo.refresh_and_verify(book)

        if audiosilo.has_usable_chapters(verified):
            store.set_status(book, "community_success")
            return "community_success"

    # External fallback.
    for provider in providers:
        result = provider.lookup(book)

        if result.kind == "temporary_error":
            store.set_retryable_error(book, result.error)
            return "lookup_failed"

        if result.kind == "ambiguous":
            store.set_status(book, "ambiguous_match")
            return "ambiguous_match"

        if result.kind != "success":
            continue

        chapters = audiosilo.validate_chapters_for_book(
            book,
            result.chapters,
        )

        if dry_run:
            return "dry_run_external_success"

        audiosilo.write_chapters(book, chapters)
        saved = audiosilo.refresh_and_verify(book)

        if not audiosilo.has_expected_chapters(saved, chapters):
            store.set_status(book, "write_failed")
            return "write_failed"

        store.set_external_success(
            book,
            provider=provider.name,
            recording_id=result.recording_id,
            chapter_count=len(chapters),
        )
        return "external_success"

    store.set_status(book, "unavailable")
    return "unavailable"
```

This skeleton deliberately leaves `has_usable_chapters`,
`obtain_chapters`, `write_chapters` and verification to the AudioSilo
client. The coding agent must implement them only after inspecting the
actual 2.6.0 API and source.

A production implementation should also use structured result types
rather than loosely shaped dictionaries, and must catch and classify
exceptions at the correct boundary.

## 12. AudioSilo API investigation checklist

Before writing the production client, inspect the running server's API
documentation/OpenAPI schema and, where necessary, the corresponding
server source code.

Verify these operations:

  -------------------------------------------------------------------------------
  Operation               Candidate route / requirement   Must establish
  ----------------------- ------------------------------- -----------------------
  List libraries          Admin libraries endpoint        Exact response and
                                                          library ID

  List books              Library books/admin listing     Pagination, path and
                                                          book identifiers

  Fetch one book          Admin book endpoint             Metadata, duration,
                                                          files and effective
                                                          chapters

  Check chapters          Book response                   Exact field and meaning
                                                          of chapters

  Community lookup        Candidate                       Availability,
                          `POST .../community-chapters`   body/query parameters
                                                          and asynchronous status

  Read community result   Book/admin response             Result fields and
                                                          source precedence

  Write chapters          Candidate `PUT .../chapters`    Exact body,
                                                          `chapter_basis`, units
                                                          and overwrite rules

  Verify write            Fetch book again                How saved chapters are
                                                          exposed

  Authentication          API key/session mechanism       Header/cookie and admin
                                                          permissions
  -------------------------------------------------------------------------------

Do not assume the candidate paths or field names in this document are
correct for 2.6.0. Confirm them.

### Specific source-code questions

If API documentation is incomplete, inspect the server implementation to
answer:

1.  What exactly counts as a book having chapters?
2.  Does the admin book response expose file, community, generated and
    edited chapters separately?
3.  Which source is selected for playback?
4.  Does the community-check endpoint run asynchronously?
5.  What does it do when a community match exists but chapter alignment
    fails?
6.  Does the chapter-writing endpoint replace all edited chapters or
    merge them?
7.  What is `chapter_basis`, and which values are accepted?
8.  Are timestamps seconds, milliseconds or another unit?
9.  How are multi-file books represented?
10. Does the Health category use the same chapter test as the
    player/API?

## 13. Safety and operational requirements

These are mandatory:

-   Never invoke Bridge, Whisper, transcription or narrative chapter
    generation.
-   Never write directly to AudioSilo's SQLite database.
-   Never overwrite existing chapters by default.
-   Never overwrite manually edited chapters.
-   Never treat provider/network errors as `unavailable`.
-   Never accept an ambiguous recording match automatically.
-   Never write unvalidated timestamps.
-   Never mark a book complete until AudioSilo confirms the saved
    chapters.
-   Use one worker initially. Increase concurrency only after measuring
    server and provider behaviour.
-   Use request timeouts, bounded retries and exponential backoff for
    transient failures.
-   Respect provider rate limits and terms of use.
-   Log book path/ID and result, but never log API tokens or
    authentication cookies.
-   Keep the sidecar database and logs outside AudioSilo's own data
    store.
-   Provide a backup/export of the proposed chapter payload before
    writing, at least during initial testing.

## 14. Test plan and rollout

Do not start by processing all 849 books.

### Phase 1: Read-only discovery

-   Connect to AudioSilo 2.6.0.
-   Identify the library.
-   List the 849 books (or the current eligible set).
-   Inspect several representative book records.
-   Confirm how effective chapters and community status are exposed.
-   Verify provider API responses independently.

Deliverable: a read-only report. No changes to AudioSilo.

### Phase 2: Lookup-only prototype

Test a small sample containing: - A book with an exact ASIN. - A book
with an ISBN but no ASIN. - A book requiring title + author search. - A
book with multiple possible editions. - A book with no external match. -
A multi-file audiobook.

Deliverable: proposed matches and chapter timelines in a report or JSON
file. No writes.

### Phase 3: Single-book write

Choose one book with no existing chapters and a strong recording match.

-   Back up/export its current chapter state.
-   Run the utility in dry-run mode.
-   Review the selected recording and chapter timeline.
-   Write the chapters through the verified AudioSilo API.
-   Re-fetch the book and inspect the player.
-   Confirm that playback seeks to the expected chapter boundaries.

Do not proceed if the saved chapters are hidden by source precedence or
the timeline is wrong.

### Phase 4: Small batch

Process 5--10 books. Review: - Correct recording selection. - Chapter
titles. - Start/end timestamps. - Multi-file handling. - Sidecar
statuses. - Re-running the same batch without duplicates or overwrites.

### Phase 5: Full library

Only after the previous phases pass: - Process the remaining eligible
books. - Keep concurrency low. - Save a report of successes, unavailable
books, ambiguous matches and retryable failures. - Make retries explicit
and safe.

## 15. Acceptance criteria

The first usable release is complete when:

1.  It can list the books in the target AudioSilo health category or
    reproduce the relevant eligibility test accurately.
2.  It can process one book by an unambiguous title/path selection.
3.  It checks existing chapters before doing any lookup.
4.  It checks AudioSilo community chapters before external providers.
5.  It can retrieve and parse external chapter data for at least one
    confirmed recording.
6.  It rejects ambiguous or invalid chapter data.
7.  It can save chapters using a verified AudioSilo 2.6.0-supported
    mechanism.
8.  It verifies the saved result by reading AudioSilo again.
9.  It can process a small batch without duplicating or overwriting
    chapters.
10. It distinguishes unavailable books from temporary failures.
11. It never starts transcription or consumes Bridge/Whisper resources.
12. It produces a useful report of every processed book and its outcome.

## 16. Decisions to preserve unless new evidence requires a change

  -----------------------------------------------------------------------
  Decision                            Reason
  ----------------------------------- -----------------------------------
  Independent Python sidecar          Avoid modifying AudioSilo and keep
                                      the utility removable

  AudioSilo API instead of direct     Preserve server validation and
  database writes                     internal consistency

  Community lookup before external    Reuse AudioSilo's native source and
  lookup                              alignment logic

  External lookup as fallback         Faster than transcription and
                                      similar to ABS

  Provider adapters                   Keep the system extensible

  Separate SQLite state database      Track batches, retries and
                                      provenance without changing
                                      AudioSilo

  One worker initially                Avoid unnecessary load and provider
                                      rate-limit problems

  Dry-run first                       Review matches and timestamps
                                      before changing library data

  No Whisper/Bridge calls             This utility exists to avoid
                                      transcription cost

  Verify after every write            An HTTP success alone does not
                                      prove chapters are usable
  -----------------------------------------------------------------------

## 17. First task for the coding agent

**Do not begin by implementing the whole script. Begin with an API
reconnaissance task.**

1.  Inspect the AudioSilo 2.6.0 API schema and relevant server source.
2.  Determine the correct authentication mechanism.
3.  Identify the audiobook library ID.
4.  Fetch one representative book from the 849-book Health category.
5.  Record the exact response fields for chapters, community lookup
    state, identifiers, duration and audio files.
6.  Verify the community-check operation.
7.  Verify the chapter-write operation and its exact payload.
8.  Test the proposed AudioSilo Meta and Audnexus lookup endpoints with
    one known book.
9.  Produce a short findings report with example responses (redacting
    credentials and personal data).
10. Only then implement the client and lookup adapters.

**Do not invent missing API fields, silently guess chapter formats, or
assume that documentation for a newer release applies to 2.6.0.** If an
operation cannot be verified, stop and report what is missing rather
than writing directly to the database.

------------------------------------------------------------------------

**End of implementation brief**


## Research update

Updated 2026-11-24: Added source-inspected Audiobookshelf chapter lookup, conversion, and persistence behaviour, with pinned source links. The sidecar's AudioSilo endpoint details remain subject to verification against the running AudioSilo 2.6.0 instance.
