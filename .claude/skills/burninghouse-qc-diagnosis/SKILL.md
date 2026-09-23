---
name: burninghouse-qc-diagnosis
description: Diagnoses Burninghouse QC results for Cam Hales — decides whether a finding is a real defect or a false positive, works out why a defect the QC missed was missed, and names the exact config change or dictionary entry that fixes it. Use this whenever a `.qc.html` report, a QC verdict, or a `qc_root/work/` frame folder is in play, and whenever Cam questions a QC result. Triggers on "is this flag real", "why did QC miss this", "the QC passed a file with a typo in it", "QC flagged a word that's fine", "tune the thresholds", "false positive on the super", "check this qc report", "why did this fail", or any question about what Burninghouse QC did or did not catch. Also use it before changing anything in `config.toml` under `[text]`, `[spelling]`, `[black]` or `[silence]`, because those numbers interact and changing one to fix a symptom often reintroduces another.
---

# Diagnosing a Burninghouse QC result

Burninghouse QC watches a folder for rendered video, checks each render, and
writes an HTML report beside it. This skill is the loop around that: reading a
result, deciding whether to trust it, and turning a disagreement into a specific
change.

## The rule everything turns on

> **Fail** means "no human needed to know this is wrong."
> **Review** means "a person has to look."
> **Pass** means "we're confident enough to say nothing."

When a case could sit on either side, it belongs in *review*. A file in review
costs someone thirty seconds. A false *fail* teaches people to ignore the tool,
and a false *pass* is the failure the tool exists to prevent.

Keep that asymmetry in mind when proposing a change: loosening a threshold so a
real defect gets caught is usually worth some review noise, while tightening one
to silence noise can quietly cost a catch.

## Start with the evidence, not the theory

Ask for the `.qc.html` report if it hasn't been given. It carries the verdict,
the file's metadata, every finding with a timecode, and a thumbnail for each
spelling flag with the word boxed. Most questions are answerable from it alone.

Two numbers in the report's header are worth reading before the findings:

- **Frames OCR'd** — roughly `duration ÷ sample_interval`. Much lower means the
  file is short or `max_frames` widened the grid.
- **Scene changes** — one or two on a multi-minute cut-heavy piece usually means
  most of the file is black or static, which reframes everything below it.

When the report isn't enough, re-run the file keeping the frames, then read them:

```bash
bhqc -c config.toml run "/path/to/render.mp4" --keep-work
python scripts/diagnose_frames.py "$(ls -dt qc_root/work/*<name>* | head -1)" \
    -c config.toml --from <start> --to <end>
```

`diagnose_frames.py` prints, for every frame in a window: what Tesseract
returned with confidence, what survived the filters, and which filter dropped
the rest — then the text runs and whether the flashed-graphic rule fired. It
answers "what was the OCR actually looking at", which is the only question that
matters when a flag looks wrong. Frames live in `<job folder>/frames`.

## A flag that looks wrong

Work down this list — the report usually names the signature directly.

**No suggested correction, high confidence.** `"lifecycle" read clearly (96%)`
with no "Did you mean". That shape means a real word the bundled US English
dictionary doesn't carry, not a misspelling. Add it to
`dictionary/custom_words.txt`, which is re-read every job — no restart. This is
the right fix for brand names, client names, jargon and modern compounds. Don't
touch thresholds for it.

**A word missing its first or last letter.** `ndependence`, `nson` from
"Branson", `offic` from "office". A frame caught mid-wipe reads a half-revealed
super as a word. These are the accepted cost of `report_min_occurrences = 1`,
which exists so subtitles get seen at all. They route to review, never fail.
Only if they become the bulk of the noise is `report_min_occurrences = 2` worth
reconsidering — and say plainly that it trades subtitle coverage away.

**Odd case shape.** `gOLOUR`, `PROFESSlONAL`. A misread character, not a
misspelling. `spelling.require_normal_case` should already drop these; if one
gets through, `text.min_confidence` is too low.

**A name.** A Title-case word beside another Title-case word is taken as a name
in a lower third and skipped. A name appearing alone, with no forename beside
it, still gets flagged — put it in the dictionary rather than loosening
`skip_proper_nouns`.

**British or Australian spelling.** Handled automatically by `variants.py`;
`colour` passes while `coulour` still fails. If one gets through, the word is
probably not in the variant map — dictionary again.

## A defect the QC missed

This is the one that matters, and it needs the frames rather than the report.
Four things swallow a real error, in the order worth checking:

**1. The text was never sampled.** The baseline grid samples every
`sample_interval` seconds. Anything on screen for less than that can fall
between two samples entirely, and anything shorter than twice it lands on a
single frame. Subtitles are the usual casualty — a caption runs about two
seconds. Check the timecode against the grid before blaming a filter. The fix is
a shorter `sample_interval`, which costs runtime roughly in proportion.

**2. The text isn't in the picture.** Only text burnt into the image is read.
Subtitles or captions on their own track are invisible, and nothing in the
report says so. Confirm with:

```bash
ffprobe -hide_banner -select_streams s -show_entries stream=index,codec_name "/path/to/render.mp4"
```

Anything listed means the QC never saw those subtitles and can't.

**3. A filter dropped it.** Run `diagnose_frames.py` over the window and read
the `dropped` lines — each names the filter and the value. Short words go to
`min_word_length`, faint reads to `min_confidence`.

**4. It is a real word in the wrong place.** `resinate` for "resonate",
`their` for "there", `form` for "from". The spell-checker returns "correctly
spelled" and always will. **Say so rather than proposing a threshold change** —
no setting catches these, and implying otherwise is worse than the miss.
Subtitles are transcribed speech, where this is the most common error of all, so
on subtitle-heavy work this tool narrows a human read rather than replacing one.

## Black, silence and mis-timed graphics

**Black or silence at the head or tail** is reported as information, because
fades and handles are on nearly every deliverable. That only holds while the run
is short: past `edge_max_duration` it stops counting as an edge artifact and
fails, because a 76-second "fade" is a bad out point, not a fade.

**A mis-timed graphic** is reported when a brief appearance is followed by the
same text held properly later. Either half alone is unremarkable; the pairing is
the defect. The detector judges "brief" by measured seconds over the unbroken
stretch of frames carrying any text, not by frame count — so a card that
rewrites itself mid-display (a line of copy, then a URL) counts as one graphic
rather than several short ones. When a candidate is found it re-samples that
window densely to measure the real edges.

If a flashed-graphic flag looks wrong, check whether the two runs really are the
same graphic (`flash_match`) before touching anything else.

## Proposing the change

Name the specific line, say what it trades away, and change one number at a
time — changing three tells you nothing about which one mattered.
`references/knobs.md` maps symptoms to settings.

Then say how to apply it, because this catches people out:

- `dictionary/custom_words.txt` — re-read every job, no restart.
- `config.toml` — needs the service restarted:
  `bhqc -c config.toml install-service --force`
- Code changes — `bhqc update`, never a bare `git pull`. A launchd agent holds
  the code it started with, so `git pull` alone updates the files and changes
  nothing about what is running, silently.

## What to say when the honest answer is "it can't"

This tool has real limits, and naming them is more useful than a threshold that
pretends otherwise:

- Spell-check cannot catch a real word in the wrong place.
- The sampling grid cannot resolve a duration below its own interval.
- Only burnt-in text is read.
- Four clean batches and one confirmed catch is not a false-negative rate — what
  the tool misses has never been measured.

Being straight about these keeps the tool trusted for what it does do.
