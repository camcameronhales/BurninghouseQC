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

# Both maps are a fenced block of column-0 section headers — "burninghouse_qc/",
# "docs/", "scripts/" — each followed by indented entries. Only the package
# section lists modules, so the others (scripts/make_sample.py and friends) must
# not be mistaken for them.
SECTION = re.compile(r"^(\S.*/)\n((?:[ \t].*\n)*)", re.MULTILINE)
MAP_ENTRY = re.compile(r"^ {2,4}(\w+\.py)", re.MULTILINE)
DOC_ENTRY = re.compile(r"^ {2,4}([\w-]+\.md)", re.MULTILINE)


def section_body(doc: str, header: str) -> str:
    """The indented lines under a column-0 section header in the map."""
    for found, body in SECTION.findall((REPO / doc).read_text()):
        if found == header:
            return body
    return ""


def package_modules() -> set[str]:
    """Every module in the package, by basename.

    Flattened deliberately: names are unique across the package, and both maps
    nest `detectors/` one level deeper, so comparing basenames keeps this test
    from caring where in the map a module is listed.
    """
    found = {path.name for path in PACKAGE.rglob("*.py")}
    return found - EXCLUDED


def listed_modules(doc: str) -> set[str]:
    return set(MAP_ENTRY.findall(section_body(doc, "burninghouse_qc/"))) - EXCLUDED


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


def test_every_doc_appears_in_the_spec_map():
    """SPEC §8 is the map a cold reader navigates by; a doc it omits is a doc
    that does not exist as far as they are concerned. service-setup.md was
    missing, which is the one covering how the thing actually runs."""
    on_disk = {path.name for path in (REPO / "docs").glob("*.md")}
    listed = set(DOC_ENTRY.findall(section_body("SPEC.md", "docs/")))
    assert not on_disk - listed, f"SPEC.md's docs map omits {sorted(on_disk - listed)}"
    assert not listed - on_disk, f"SPEC.md's docs map lists missing {sorted(listed - on_disk)}"


def test_every_cli_subcommand_appears_in_the_spec_map():
    """`uninstall` shipped and was documented nowhere."""
    source = (REPO / "burninghouse_qc" / "cli.py").read_text()
    subcommands = set(re.findall(r'add_parser\(\s*"([\w-]+)"', source))
    # The cli.py entry runs across continuation lines until the next module.
    entry = re.search(r"^  cli\.py(.*?)(?=^  \w+\.py)",
                      section_body("SPEC.md", "burninghouse_qc/"),
                      re.MULTILINE | re.DOTALL)
    assert entry, "could not find the cli.py entry in SPEC.md's code map"
    missing = {name for name in subcommands if name not in entry.group(1)}
    assert not missing, f"SPEC.md's cli.py entry does not list {sorted(missing)}"
