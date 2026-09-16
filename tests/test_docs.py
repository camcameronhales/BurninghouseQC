"""The code maps in README.md and SPEC.md have to match the package.

Two hand-maintained lists of the same facts drift, and these two had — in
different directions. Before this test, SPEC was missing `access.py`,
`ffmpeg_tools.py` and `status.py`; README was missing `notify.py` and
`service.py`; and neither mentioned `status.py` at all, which is the whole
"is it working?" surface of an unattended service.

A stale map is the same shape of bug as the one SPEC §7 describes: the docs
assert something about what exists and nothing checks the assertion. So this
checks it, in both directions — a module with no line, and a line with no
module. The second half is what catches a map still advertising something
that has been deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "burninghouse_qc"
DOCS = ("README.md", "SPEC.md")

# `__init__.py` is a sixteen-line guard that raises on Python older than 3.11.
# It is not a component, and a line for it in either map would be noise.
EXCLUDED = {"__init__.py"}

# Map entries are indented two or four spaces inside a fenced block:
#   scan.py           one decode pass shared by black + scene detection
#     black.py        blackdetect
MAP_ENTRY = re.compile(r"^ {2,4}(\w+\.py)", re.MULTILINE)


def package_modules() -> set[str]:
    """Every module in the package, by basename.

    Flattened deliberately: names are unique across the package, and both maps
    nest `detectors/` one level deeper, so comparing basenames keeps this test
    from caring where in the map a module is listed.
    """
    found = {path.name for path in PACKAGE.rglob("*.py")}
    return found - EXCLUDED


def listed_modules(doc: str) -> set[str]:
    return set(MAP_ENTRY.findall((REPO / doc).read_text())) - EXCLUDED


@pytest.mark.parametrize("doc", DOCS)
def test_every_module_appears_in_the_code_map(doc):
    missing = package_modules() - listed_modules(doc)
    assert not missing, (
        f"{doc}'s code map does not mention {sorted(missing)}. "
        f"Add a line for each, or add it to EXCLUDED with a reason."
    )


@pytest.mark.parametrize("doc", DOCS)
def test_the_code_map_does_not_list_modules_that_are_gone(doc):
    phantom = listed_modules(doc) - package_modules()
    assert not phantom, (
        f"{doc}'s code map still lists {sorted(phantom)}, which no longer exists."
    )


def test_the_two_maps_agree_with_each_other():
    readme, spec = (listed_modules(doc) for doc in DOCS)
    assert readme == spec, (
        f"the maps disagree — only in README: {sorted(readme - spec)}, "
        f"only in SPEC: {sorted(spec - readme)}"
    )
