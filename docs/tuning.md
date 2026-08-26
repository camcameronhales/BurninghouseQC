# Tuning the thresholds

Every number in this document is a **first guess**. They were set against
synthetic test footage, not against Burninghouse renders, and the spec (§6)
explicitly leaves them to be adjusted during the pilot. This is how to do that.

## The rule the whole thing turns on

> **Fail** means "no human needed to know this is wrong."
> **Review** means "a person has to look."
> **Pass** means "we're confident enough to say nothing."

When you're unsure which side a case belongs on, make it *review*. A file in
the review folder costs someone thirty seconds. A false *fail* teaches people
to ignore the tool, and a false *pass* is the failure mode the tool exists to
prevent.

## The pilot loop

Run the QC alongside the existing manual check for a couple of weeks — not
instead of it — and keep a note of every disagreement.

```bash
# Check a file without moving it, and keep the sampled frames for inspection
bhqc scan "/renders/Client_Spot_v3.mov" --keep-work
```

`--keep-work` leaves the extracted frames in `qc_root/work/`, which answers the
only question that matters when a flag looks wrong: *what was the OCR actually
looking at?*

Then classify what you find:

| What happened | What to change |
| --- | --- |
| Real error, but only routed to *review* | lower `text.fail_confidence`, or set `fail_min_occurrences = 1` |
| Clean file flagged as *fail* | raise `text.min_confidence` first, then `fail_confidence` |
| Same brand/client word flagged repeatedly | add it to `dictionary/custom_words.txt` — don't touch thresholds |
| A misspelling was missed entirely | lower `text.sample_interval` (denser sampling) |
| Intentional cut-to-black failed the file | raise `black.fail_duration` |
| Deliberate pause failed the file | raise `silence.fail_duration` |
| Fades flagged at the head/tail | raise `black.edge_grace` / `silence.edge_grace` |
| Jobs take too long | raise `text.sample_interval`, or lower `text.max_frames` |
| A short super was only flagged as *review* | lower `text.sample_interval` so it's sampled twice, or set `fail_min_occurrences = 1` |
| Renders on a share are never noticed | `watcher.use_polling = "true"` (see service-setup.md §6) |

Change **one** number at a time and re-run the same file. Changing three at
once tells you nothing about which one mattered.

## What each knob actually does

### `[text]` — the ones you'll touch most

- **`min_confidence` (70)** — the noise gate. Tesseract scores every word it
  reads; below this the token is thrown away before spell-checking. Raise it if
  you're getting flags on garbled nonsense (a symptom of OCR reading texture,
  logos or motion blur as text). Lowering it below ~60 makes the tool very
  noisy on graphics-heavy work.
- **`fail_confidence` (85)** — the fail gate. A misspelling read *less*
  confidently than this can only ever route to review. This is your main dial
  for "the tool is too aggressive" vs "the tool is too soft".
- **`fail_min_occurrences` (2)** — how many separate sampled frames the word
  must appear in before it can fail a file. This is what stops a single bad OCR
  read from failing a render, and it works because a real title card is on
  screen for several seconds and gets sampled more than once. If supers in your
  work sit on screen for under two seconds, either set this to `1` (and lean on
  `fail_confidence`) or lower `sample_interval` so short cards get sampled
  twice.
- **`sample_interval` (1.5s)** — the thoroughness/runtime trade-off, and the
  one with a real interaction: **a card must be on screen for at least
  `sample_interval x fail_min_occurrences` to be fail-eligible.** At the
  defaults that's 3 seconds. A super held for less than that can only ever
  reach *review*, however clearly the OCR read it. If house style uses shorter
  supers, lower this before touching anything else. QC time scales almost
  linearly with the frame count, so halving it roughly doubles the runtime.
- **`scene_threshold` (0.35)** — FFmpeg's scene-change score cut-off. Lower
  finds more cuts (more frames, slower); higher finds fewer. Worth lowering for
  slow, graded, low-contrast work where cuts score weakly.
- **`tesseract_psm` (11)** — page segmentation mode. `11` (sparse text) suits
  burnt-in graphics scattered over a frame. Try `6` (uniform block) if the work
  is mostly full-frame title cards, and `3` for dense text like credit rolls.
- **`ocr_target_height` (1440)** — frames are rescaled to roughly this height
  before OCR, because small type reads poorly at 1:1 but upscaling an already
  large frame just burns time. 720p gets 2x, 1080p ~1.33x, 1440p and above are
  left alone (`ocr_min_scale = 1.0` means never downscale, so small type in a
  4K master is never shrunk below what Tesseract can read). Raise toward 2160
  if small lower-thirds are being missed, at roughly quadratic cost in time.

### `[black]` and `[silence]`

- **`fail_duration`** — the boundary between "a deliberate creative choice" and
  "something went wrong". If the house style uses cuts to black, raise
  `black.fail_duration` above the longest intentional one.
- **`edge_grace` (1.5s)** — how much of the head and tail is treated as
  fade/handle territory, where black and silence are expected and downgraded to
  review. Raise it if masters ship with long black slates or silent handles.
- **`silence.noise_db` (-50dB)** — what counts as silence. Room tone and a
  noise floor sit above this; true digital silence is well below it. Raise
  toward `-40` to catch dropouts that still carry a faint floor.
- **`silence.missing_audio` (`review`)** — what to do with a file that has no
  audio stream at all. Set to `fail` if every deliverable must have audio, or
  `ignore` if silent deliverables are routine.

### `[watcher]`

- **`stability_checks` × `poll_interval`** — how long a file must sit unchanged
  before it's considered a finished render (default ~10s). Raise both on a slow
  network share, where writes can stall long enough to look finished.

## What it costs to run

Measured on a 5-minute 1080p clip with the shipped defaults: **160 seconds**,
about 30s of QC per minute of video. A 10-minute master lands near 5 minutes.
Roughly half of that is OCR, a quarter frame extraction, a quarter the
black/scene decode pass.

Two caveats. Those figures come from the Linux container this was built in,
against deliberately busy synthetic footage — real graded material OCRs faster,
and an Apple Silicon Mac will differ. **Re-measure on the actual machine early
in the pilot**, with:

```bash
time bhqc scan "/renders/a_typical_master.mov"
```

If it turns out slower than the render itself, the order to attack it in is:
raise `sample_interval`, then lower `ocr_target_height`, then raise
`scene_threshold` (fewer cuts detected means fewer follow-up frames). Lowering
`max_frames` is the blunt instrument — it caps the work but thins coverage on
longer clips.

## Adding words to the dictionary

`dictionary/custom_words.txt` — one entry per line, `#` for comments, case
ignored, multi-word entries allowed. It's re-read on every job, so adding a
client name takes effect on the next file with no restart.

Don't add British/Australian spellings — `colour`, `organise`, `centre` and the
rest are already accepted by `burninghouse_qc/variants.py`.

Do add: client and brand names, product names, presenter surnames, industry
jargon, and any deliberate stylisation the house uses.

## When to consider cloud OCR

Local Tesseract is the v1 choice and it should stay that way unless the pilot
shows it can't read your graphics — heavily stylised type, script faces, text
over busy footage, or low-contrast grades. Before reaching for a paid API, try
in this order: raise `ocr_target_height`, change `tesseract_psm`, then sample more
densely. If misses persist on frames where a person can plainly read the text,
that's the evidence that the local engine isn't enough.
