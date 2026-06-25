# Google Sheets Spec — YouTube Tracker (for Gemini)

Paste this into Gemini in Google Sheets. It describes the **two tabs** to create and the **columns**
in each. That's all you need to build. Keep it simple.

There are two sheets. You add a YouTube link from your phone → it lands in **Inbox**. A separate
process fills in the results → they appear in **Library**. (How the results get filled in is handled
elsewhere and is not your concern here — just create the columns so the data has a home.)

---

## Tab 1 — "Inbox"

The drop box. A row appears here every time a YouTube link is shared from a phone. Keep it minimal —
only the first column is filled when a link is added; the rest are status fields that get updated
later.

| Column | Filled by | Notes |
|---|---|---|
| **URL** | you (share button) | the YouTube link — the only thing entered when adding |
| **Added** | you (share button) | date/time the link was added |
| **Status** | the processor | New → Processing → Done → Failed (dropdown; default "New") |
| **Note** | the processor | short message, e.g. an error reason if it failed |

Gemini setup for this tab:
- Make **Status** a dropdown: New, Processing, Done, Failed. Colour: Done = green, Failed = red,
  Processing = amber, New = grey.
- Freeze the header row.
- New links should append to the next empty row (no manual sorting needed).

---

## Tab 2 — "Library"

The finished archive — one row per processed video, written back when processing completes. This is
the watchable dashboard.

| Column | Notes |
|---|---|
| **Thumbnail** | inline image: `=IMAGE("<thumbnail_url>")` — renders because YouTube thumbnails are public |
| **Title** | clickable: `=HYPERLINK("<url>","<title>")` |
| **Channel** | clickable: `=HYPERLINK("<channel_url>","<name>")` |
| **Category** | ai-agents / seo / productivity … (dropdown) |
| **Duration** | e.g. 32:42 |
| **Video ID** | stable YouTube ID (used to match back to the Inbox row) |
| **Status** | Done / Failed |
| **Date Processed** | YYYY-MM-DD |
| **Transcript?** | Yes / No |
| **Segments** | transcript segment count |
| **Screenshots** | number of screenshots captured |
| **Clips** | number of demo clips extracted |
| **Top Screenshot** | best demo frame (image once hosted publicly — see note) |
| **Folder** | link to where the files live |
| **Rating** | your 1–5 score (dropdown) |
| **Key Takeaway** | one-line summary |
| **Notes** | anything else |

Gemini setup for this tab:
- Row height ~80px so thumbnails are visible; freeze the header row and the Title column.
- **Status**: Done = green, Failed = red.
- **Category**: dropdown from the unique values in the column.
- **Rating**: 1–5 dropdown (show as stars if you can).
- Add a filter view to filter by Category and Status.
- Add a small summary block at the top: total videos, # Done, # Failed, total screenshots,
  total clips, and a per-Category count.

---

## Note on images

`=IMAGE()` only renders **public http(s) URLs**.
- **Thumbnail** works out of the box — YouTube hosts those publicly.
- **Top Screenshot** comes from local files, so it will only render once those files are hosted at a
  public URL. Until then it shows a path. (Leave the column as-is; it'll light up later.)

---

That's the whole spec: two tabs — **Inbox** (URL, Added, Status, Note) and **Library** (the columns
above). Build those and you're done.
