import unittest

import helpers  # noqa: F401  lib 경로 설정
from orch.globs import any_overlap, matches, overlaps


class GlobTest(unittest.TestCase):
    def test_matches(self):
        self.assertTrue(matches("src/a/b.ts", "src/**"))
        self.assertTrue(matches("src/x.ts", "src/**/x.ts"))
        self.assertTrue(matches("src/a/x.ts", "src/**/x.ts"))
        self.assertTrue(matches("package.json", "package.json"))
        self.assertTrue(matches("docs/a.md", "docs/*.md"))
        self.assertFalse(matches("src", "src/**"))
        self.assertFalse(matches("docs/a/b.md", "docs/*.md"))

    def test_overlaps(self):
        self.assertTrue(overlaps("src/**", "src/auth/**"))
        self.assertTrue(overlaps("docs/**", "docs/qa/**"))
        self.assertTrue(overlaps("src/app.ts", "src/**"))
        self.assertTrue(overlaps("a.txt", "a.txt"))
        self.assertFalse(overlaps("docs/planning/**", "docs/design/**"))
        self.assertFalse(overlaps("docs/plan/**", "docs/planning/**"))
        self.assertFalse(overlaps("package.json", "src/**"))

    def test_any_overlap(self):
        self.assertEqual(any_overlap(["src/**", "x.md"], ["tests/**", "src/ui/**"]), [("src/**", "src/ui/**")])


if __name__ == "__main__":
    unittest.main()
