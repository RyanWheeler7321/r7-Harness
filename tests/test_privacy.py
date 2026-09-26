from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    ".ahk",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".patch",
    ".py",
    ".sh",
    ".svg",
    ".ts",
    ".txt",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {".gitignore", "AGENTS", "LICENSE", "NOTICE", "OMP-LICENSE", "r7harness"}

# local paths and secrets that shouldn't be in anything published
FORBIDDEN = {
    "Windows user-drive path": re.compile(r"(?:\b[A-Za-z]:|/mnt/[A-Za-z])[\\/]+Users[\\/]", re.IGNORECASE),
    "cloud access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "provider secret": re.compile(r"\b(?:sk|rk|pk)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    "source-control secret": re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}\b", re.IGNORECASE),
    "service secret": re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    "credential-bearing header": re.compile(
        r"\b(?:authorization|cookie)\s*[:=]\s*['\"]?(?:bearer\s+)?[A-Za-z0-9._~-]{16,}", re.IGNORECASE
    ),
    "stored access material": re.compile(
        r"\b(?:access|refresh|session)[_-]?(?:token|key|id)\s*[:=]\s*['\"][A-Za-z0-9._~-]{16,}", re.IGNORECASE
    ),
}


def text_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.resolve() != SELF
        and ".git" not in path.relative_to(ROOT).parts
        and (path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_FILENAMES)
    )


def published_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".patch":
        # context and removed lines are upstream OMP code, only check what the patch adds
        text = "\n".join(line for line in text.splitlines() if not line.startswith((" ", "-", "@@")))
    return text


class PrivacyTest(unittest.TestCase):
    def test_published_text_contains_no_private_material(self) -> None:
        findings: list[str] = []
        for path in text_files():
            text = published_text(path)
            for label, pattern in FORBIDDEN.items():
                match = pattern.search(text)
                if match:
                    findings.append(f"{path.relative_to(ROOT)}: {label}: {match.group(0)!r}")

        self.assertEqual(findings, [], "\n".join(findings))


if __name__ == "__main__":
    unittest.main()
