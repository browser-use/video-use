"""Guard the public editing contract in SKILL.md.

Two failure modes this protects against when several agents edit the file:
a hard rule silently disappears or gets renumbered, and a helper or reference
path is documented but no longer exists in the tree.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"

# Append-only. Add new rules to the end of this list when SKILL.md gains one.
HARD_RULES = [
    "Subtitles are applied LAST in the filter chain",
    "Per-segment extract",
    "30ms audio fades at every segment boundary",
    "Overlays use `setpts=PTS-STARTPTS+T/TB`",
    "Master SRT uses output-timeline offsets",
    "Never cut inside a word.",
    "Pad every cut edge.",
    "Word-level verbatim ASR only.",
    "Cache transcripts per source.",
    "Parallel sub-agents for multiple animations.",
    "Strategy confirmation before execution.",
    "All session outputs in `<videos_dir>/edit/`.",
    "Captions transcribe audible speech only.",
]

# matches backticked paths under references helpers or skills
PATH_PATTERN = re.compile(r"`((?:references|helpers|skills)/[A-Za-z0-9_./-]+)`")
# matches a bare helper script name at the start of a backticked command
HELPER_PATTERN = re.compile(r"`([A-Za-z0-9_]+\.py)\b")


# slice out the body of the hard rules section up to the next second level heading
def hard_rules_section(text: str) -> str:
    # dotall plus multiline so the lazy group spans lines and the heading anchors match at line starts
    match = re.search(r"^## Hard Rules.*?$(.*?)^## ", text, re.S | re.M)
    assert match, "SKILL.md must contain a '## Hard Rules' section"
    return match.group(1)


# test case that reads skill md once per test and checks its contract
class SkillContractTests(unittest.TestCase):
    # load the skill md text for each test
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    # numbered bold rule titles must be consecutive and each expected rule must still sit at its index
    def test_hard_rules_are_present_in_order(self):
        section = hard_rules_section(self.text)
        # capture the number and bold title of each numbered list item
        items = re.findall(r"^(\d+)\. \*\*(.+?)\*\*", section, re.M)
        numbers = [int(number) for number, _ in items]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)), "rules must be numbered consecutively")
        self.assertGreaterEqual(len(items), len(HARD_RULES), "a hard rule was removed")
        for index, expected in enumerate(HARD_RULES):
            self.assertIn(expected, items[index][1], f"rule {index + 1} changed or moved")

    # every backticked helper or reference path in skill md must exist on disk
    def test_referenced_paths_exist(self):
        missing = sorted(
            path
            for path in set(PATH_PATTERN.findall(self.text))
            if not (ROOT / path).exists()
        )
        self.assertEqual(missing, [], "SKILL.md names paths that do not exist")

    # every bare helper script named in a backticked command must exist under helpers
    def test_bare_helper_names_exist(self):
        missing = sorted(
            name
            for name in set(HELPER_PATTERN.findall(self.text))
            if not (ROOT / "helpers" / name).exists()
        )
        self.assertEqual(missing, [], "SKILL.md names helper scripts that do not exist")


if __name__ == "__main__":
    unittest.main()
