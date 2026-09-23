# Symptom → setting

Every number here is tunable in `config.toml`. Change **one at a time** and
re-run the same file; changing three tells you nothing about which mattered.
Defaults shown are as shipped — read the live `config.toml` for what a machine
is actually running, since the two drift.

## On-screen text

| What happened | What to change |
| --- | --- |
| Real error, only routed to *review* | lower `text.fail_confidence` (85), or `text.fail_min_occurrences = 1` |
| Clean file flagged as *fail* | raise `text.min_confidence` (70) first, then `fail_confidence` |
| Flag on a garbled word (`gOLOUR`, `PROFESSlONAL`) | `spelling.require_normal_case` should catch it; if not, raise `text.min_confidence` |
| Same brand or client word flagged repeatedly | add to `dictionary/custom_words.txt` — **don't touch thresholds** |
| Flagged word has no suggested correction | a real word missing from the dictionary — dictionary again |
| A name in a lower third flagged | `spelling.skip_proper_nouns` should skip it; a name appearing alone needs the dictionary |
| A misspelling missed entirely | lower `text.sample_interval` (1.0), or check it was on screen at all |
| Subtitle typos missed | `text.sample_interval` — a caption runs ~2s and needs the grid below that |
| A fragment flagged (`nson`, `offic`, `ndependence`) | the cost of `text.report_min_occurrences = 1`; set to 2 to suppress, losing subtitle coverage |
| Short words never checked (`For`, `on`) | `text.min_word_length` (4) — lowering it is noisy, consider it a known limit |
| Jobs take too long | raise `text.sample_interval`, or lower `text.max_frames` (900) |

`sample_interval` and `max_frames` interact: the grid widens to fit the budget,
so `max_frames` too low silently coarsens long files. At 1.0s, 900 frames holds
the full cadence to about twelve minutes.

## Black

| What happened | What to change |
| --- | --- |
| Intentional cut-to-black failed the file | raise `black.fail_duration` (0.5) |
| A fade at the head or tail was flagged | raise `black.edge_grace` (1.5) |
| A long dead tail was called "normal for a fade" | lower `black.edge_max_duration` (10.0) |
| Fades cluttering the report | `black.edge_severity = "ignore"` drops them entirely |

## Silence

| What happened | What to change |
| --- | --- |
| A deliberate pause failed the file | raise `silence.fail_duration` (3.0) |
| Handles at head or tail flagged | raise `silence.edge_grace` (1.5) |
| Most of the file silent, reported as "normal handles" | lower `silence.edge_max_duration` (30.0) |
| A file with no audio track | `silence.missing_audio` — `"fail"`, `"review"` or `"ignore"` |

## Mis-timed graphics

| What happened | What to change |
| --- | --- |
| A graphic held normally called a flash | raise `text.flash_max_span` (2.0), or check the two runs really are one graphic |
| A real flash not paired with its proper appearance | lower `text.flash_match` (0.6) |
| The proper appearance too short to qualify | lower `text.flash_min_proper_frames` (2) |
| Brief graphics timed imprecisely | lower `text.flash_refine_interval` (0.25); 0 disables the second pass |
| Too many candidates re-sampled on a busy file | lower `text.flash_refine_max` (8) |

## Watching and routing

| What happened | What to change |
| --- | --- |
| Renders on a share never noticed | `watcher.use_polling = "true"` (a string, not a boolean) |
| Reports should not sit beside the render | `routing.mode = "report_only"` |
| A render QC'd mid-write | raise the stability poll count in `[watcher]` |

There is no mode that moves or renames a render. `alongside` (the default) and
`report_only` are the only two, and neither touches the file.
