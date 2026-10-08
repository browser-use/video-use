"""guards the comment convention across every python file in the repo
each file opens with a module docstring and every def or class has one plain comment line above it
comments must not carry punctuation so they stay short and readable at a glance
"""

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", "media", "edit"}
PUNCTUATION = re.compile(r"[.,:;()\"'`]")
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


# yield every python file in the repo that is not inside a skipped directory
def python_files():
    for path in ROOT.rglob("*.py"):
        if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            yield path


# return the problems found in one file as short strings
def audit(text):
    lines = text.splitlines()
    problems = []
    # the file opens with a docstring and not with a block of hash comments
    first = [line for line in lines[:6] if line.strip() and not line.startswith("#!")]
    if first and first[0].startswith("#"):
        problems.append("hash comment header should be a module docstring")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        problems.append("file does not parse")
        return problems
    if ast.get_docstring(tree) is None:
        problems.append("missing module docstring")
    comments = real_comments(text)
    for node in sorted(definitions(tree), key=lambda item: item.lineno):
        # decorators sit between the comment and the definition so look above the first decorator
        start = min([node.lineno, *[item.lineno for item in node.decorator_list]])
        above = start - 1
        if above not in comments or not comments[above]:
            problems.append(f"line {node.lineno} has no comment above {node.name}")
            continue
        if PUNCTUATION.search(comments[above]):
            problems.append(f"line {above} comment contains punctuation")
    return problems


# yield every function and class node in the tree so text inside strings never counts as a definition
def definitions(tree):
    for node in ast.walk(tree):
        if isinstance(node, DEFINITIONS):
            yield node


# map line number to comment text for lines that hold nothing but a real comment token
def real_comments(text):
    comments = {}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT and token.line.strip().startswith("#"):
            comments[token.start[0]] = token.string.lstrip("#").strip()
    return comments


# one test that scans the whole repo so a single failure lists every offending file
class CommentStyleTests(unittest.TestCase):
    # every python file must satisfy the header and per definition comment rules
    def test_every_python_file_follows_the_convention(self):
        failures = {}
        for path in python_files():
            problems = audit(path.read_text(encoding="utf-8"))
            if problems:
                failures[str(path.relative_to(ROOT))] = problems[:5]
        self.assertEqual(failures, {}, "comment convention violations")


if __name__ == "__main__":
    unittest.main()
