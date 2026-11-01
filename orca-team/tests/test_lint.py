import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from orch.lint import DESIGN_ROLES, MAX_BYTES, MAX_FINDINGS, design_lint, hue_family, scan

VS16 = chr(0xFE0F)  # 이모지 표시 선택자. 소스에서 보이지 않으므로 이름으로 쓴다


class CountingText(str):
    """count 호출 수를 세는 문자열. scan 이 그라디언트 매치마다 앞부분을 다시 세는지(줄 번호) 본다."""

    def count(self, *args):
        self.count_calls = getattr(self, "count_calls", 0) + 1
        return super().count(*args)


class DesignLintTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orca-team-lint-")).resolve()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel

    def heads(self, *rels):
        """"파일:줄: 패턴" 부분만(발췌 앞 첫 괄호까지)."""
        return [item.split("(", 1)[0] for item in design_lint(self.root, list(rels))]

    def test_design_roles(self):
        self.assertEqual(DESIGN_ROLES, ("director", "designer", "developer"))

    def test_emoji_is_reported_once_per_line(self):
        rel = self.put("src/ui/home.tsx", "<h1>홈</h1>\n<p>🚀 빠르게 시작 ✨</p>\n<p>→ 다음 • 항목</p>\n")
        self.assertEqual(design_lint(self.root, [rel]), ["src/ui/home.tsx:2: 이모지(🚀)"])

    def test_emoji_presentation_and_variation_selector_only(self):
        """텍스트 기호(✓ ★ ❯ ➜ ⬆ ▶)는 잡지 않는다. BMP 의 이모지 표시 문자와 FE0F(이모지 표시 선택자)가 붙은 문자는 잡는다."""
        rel = self.put("src/ui/icons.tsx", "✓ ★ ❯ ➜ ⬆ ▶ 다음\n✅ 완료\n⏰ 알림\n⏳ 대기\n⬛ 칸\n⭐ 별\n"
                                           "▶️ 재생\n⬆️ 위로\n✔️ 확인\n☀️ 맑음\n")
        self.assertEqual(design_lint(self.root, [rel]), [
            "src/ui/icons.tsx:2: 이모지(✅)", "src/ui/icons.tsx:3: 이모지(⏰)", "src/ui/icons.tsx:4: 이모지(⏳)",
            "src/ui/icons.tsx:5: 이모지(⬛)", "src/ui/icons.tsx:6: 이모지(⭐)", "src/ui/icons.tsx:7: 이모지(▶️)",
            "src/ui/icons.tsx:8: 이모지(⬆️)", "src/ui/icons.tsx:9: 이모지(✔️)", "src/ui/icons.tsx:10: 이모지(☀️)"])

    def test_invisible_left_border_is_not_reported(self):
        rel = self.put("src/ui/tabs.css", ".tab { border-left: 4px solid transparent; }\n"
                                          ".a { border-left: 4px none #2563eb; }\n"
                                          ".b { border-inline-start: 3px hidden #2563eb; }\n"
                                          ".on { border-left: 4px solid #2563eb; }\n")
        self.assertEqual(self.heads(rel), ["src/ui/tabs.css:4: 왼쪽 줄 강조"])

    def test_invisible_keyword_must_stand_alone(self):
        """사용자 정의 속성 이름(var(--none))이나 함수 인자(var(--accent, transparent)) 안의 낱말은 보이지 않는 선이 아니다."""
        rel = self.put("src/ui/vars.css", ".a { border-left: 4px solid var(--none); }\n"
                                          ".b { border-left: 4px solid var(--x-hidden); }\n"
                                          ".c { border-left: 4px solid var(--accent, transparent); }\n"
                                          ".d { border-left: 4px solid transparent; }\n"
                                          ".e { border-left: 4px solid TRANSPARENT; }\n")
        self.assertEqual(self.heads(rel), ["src/ui/vars.css:1: 왼쪽 줄 강조", "src/ui/vars.css:2: 왼쪽 줄 강조",
                                           "src/ui/vars.css:3: 왼쪽 줄 강조"])

    def test_whitespace_before_variation_selector_is_not_emoji(self):
        rel = self.put("src/ui/stray.tsx", f"<p>a {VS16} b</p>\n<p>\t{VS16}</p>\n<p>▶{VS16} 재생</p>\n")
        self.assertEqual(design_lint(self.root, [rel]), [f"src/ui/stray.tsx:3: 이모지(▶{VS16})"])

    def test_left_border_keyword_widths(self):
        """너비 낱말 medium(3px)·thick(5px)은 2px 이상, thin(1px)은 아니다."""
        css = self.put("src/ui/kw.css", ".a { border-left-width: thick; }\n.b { border-inline-start-width: medium; }\n"
                                        ".c { border-left-width: thin; }\n.d { border-left: thick solid #2563eb; }\n"
                                        ".e { border-left: thin solid #2563eb; }\n.f { border-left-width: var(--thick); }\n")
        jsx = self.put("src/ui/Kw.tsx", "const a = { borderLeftWidth: 'thick' };\nconst b = { borderInlineStartWidth: \"thin\" };\n"
                                        "const c = { borderLeft: 'medium solid #2563eb' };\n")
        self.assertEqual(self.heads(css, jsx), ["src/ui/kw.css:1: 왼쪽 줄 강조", "src/ui/kw.css:2: 왼쪽 줄 강조",
                                                "src/ui/kw.css:4: 왼쪽 줄 강조", "src/ui/Kw.tsx:1: 왼쪽 줄 강조",
                                                "src/ui/Kw.tsx:3: 왼쪽 줄 강조"])

    def test_left_border_width_alone(self):
        css = self.put("src/ui/note.css", ".a { border-left-width: 4px; }\n.b { border-inline-start-width: 0.25rem; }\n"
                                          ".c { border-left-width: 1px; }\n")
        jsx = self.put("src/ui/Note.tsx", "const a = { borderLeftWidth: 4 };\nconst b = { borderInlineStartWidth: '3px' };\n"
                                          "const c = { borderLeftWidth: 1, borderInlineStartWidth: \"1px\" };\n")
        self.assertEqual(self.heads(css, jsx), ["src/ui/note.css:1: 왼쪽 줄 강조", "src/ui/note.css:2: 왼쪽 줄 강조",
                                                "src/ui/Note.tsx:1: 왼쪽 줄 강조", "src/ui/Note.tsx:2: 왼쪽 줄 강조"])

    def test_tailwind_start_and_arbitrary_left_border(self):
        rel = self.put("src/ui/Callout.tsx", '<div className="border-s-4 border-s-blue-500">a</div>\n'
                                             '<div className="border-l-[3px] border-blue-500">b</div>\n'
                                             '<div className="border-s-[0.25rem] border-gray-300">c</div>\n'
                                             '<div className="border-l-[1px] border-blue-500">d</div>\n'
                                             '<div className="border-s-2 pl-4">e</div>\n'
                                             '<div className="border-l-2 border-l-[#2563eb]">f</div>\n')
        self.assertEqual(self.heads(rel), ["src/ui/Callout.tsx:1: 왼쪽 줄 강조", "src/ui/Callout.tsx:2: 왼쪽 줄 강조",
                                           "src/ui/Callout.tsx:3: 왼쪽 줄 강조", "src/ui/Callout.tsx:6: 왼쪽 줄 강조"])

    def test_oklch_hue_family(self):
        """Tailwind v4 기본 팔레트 500 값. sky·blue·indigo 는 파랑, violet·purple·fuchsia 는 보라."""
        for color, family in (("oklch(68.5% 0.169 237.323)", "blue"), ("oklch(62.3% 0.214 259.815)", "blue"),
                              ("oklch(58.5% 0.233 277.117)", "blue"), ("oklch(60.6% 0.25 292.717)", "purple"),
                              ("oklch(62.7% 0.265 303.9)", "purple"), ("oklch(66.7% 0.295 322.15)", "purple"),
                              ("oklch(0.606 0.25 292.717 / 0.5)", "purple"), ("oklch(60% 0.02 280)", None),
                              ("oklch(70% 0.2 30)", None)):
            with self.subTest(color=color):
                self.assertEqual(hue_family(color), family)

    def test_oklch_gradients(self):
        rel = self.put("src/ui/hero.css", ".a { background: linear-gradient(oklch(62.3% 0.214 259.815), "
                                          "oklch(62.7% 0.265 303.9)); }\n"
                                          ".b { background: linear-gradient(90deg, oklch(0.685 0.169 237.323) 0%, "
                                          "oklch(0.606 0.25 292.717 / 0.8) 100%); }\n"
                                          ".c { background: linear-gradient(oklch(68.5% 0.169 237.323), "
                                          "oklch(58.5% 0.233 277.117)); }\n"
                                          ".d { background: linear-gradient(oklch(62.3% 0.214 259.815), oklch(60% 0.02 300)); }\n")
        self.assertEqual(self.heads(rel), ["src/ui/hero.css:1: 파랑·보라 그라디언트", "src/ui/hero.css:2: 파랑·보라 그라디언트"])

    def test_worst_case_gradients_do_not_rescan_text(self):
        """1MB 안에 그라디언트가 줄마다 있어도 줄 번호를 매치마다 처음부터 세지 않는다(고치기 전에는 매치마다
        text.count 를 불러 O(n·m), 1MB 에서 2~4초). 시간 대신 count 호출이 없는지 본다."""
        line = "linear-gradient(blue, purple)\n"
        lines = MAX_BYTES // len(line)
        text = CountingText(line * lines)
        all_found = scan("src/ui/many.css", text)
        self.assertEqual(getattr(text, "count_calls", 0), 0)
        self.assertEqual(len(all_found), lines)
        self.assertEqual(all_found[-1].split("(", 1)[0], f"src/ui/many.css:{lines}: 파랑·보라 그라디언트")
        rel = self.put("src/ui/many.css", line * lines)
        found = design_lint(self.root, [rel])
        self.assertEqual(len(found), MAX_FINDINGS + 1)
        self.assertEqual(found[0], "src/ui/many.css:1: 파랑·보라 그라디언트(linear-gradient(blue, purple))")
        self.assertEqual(found[MAX_FINDINGS - 1].split("(", 1)[0], f"src/ui/many.css:{MAX_FINDINGS}: 파랑·보라 그라디언트")
        self.assertEqual(found[-1], f"… 외 {lines - MAX_FINDINGS}건")

    def test_left_border_accent_in_css_and_jsx(self):
        css = self.put("src/ui/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n"
                                          ".quote { border-inline-start: 0.25rem solid var(--accent); }\n"
                                          ".table td { border-left: 1px solid #e5e7eb; }\n"
                                          ".none { border-left: 0; }\n")
        jsx = self.put("src/ui/Note.jsx", "const s = { borderLeft: '4px solid #7c3aed', padding: 12 };\n")
        self.assertEqual(design_lint(self.root, [css])[0], "src/ui/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)")
        self.assertEqual(self.heads(css, jsx), ["src/ui/card.css:2: 왼쪽 줄 강조", "src/ui/card.css:4: 왼쪽 줄 강조",
                                                "src/ui/Note.jsx:1: 왼쪽 줄 강조"])

    def test_tailwind_left_border_needs_width_and_color(self):
        rel = self.put("src/ui/Callout.tsx", '<div className="border-l-4 border-blue-500 pl-4">a</div>\n'
                                             '<div className="border-l-4 pl-4">b</div>\n'
                                             '<td className="border-l border-gray-200">c</td>\n')
        self.assertEqual(self.heads(rel), ["src/ui/Callout.tsx:1: 왼쪽 줄 강조"])

    def test_blue_purple_css_gradients(self):
        rel = self.put("src/ui/hero.css", ".hero {\n  background: linear-gradient(\n    135deg,\n    #667eea 0%,\n"
                                          "    #764ba2 100%\n  );\n}\n"
                                          ".ok { background: linear-gradient(#3b82f6, #1d4ed8); }\n"
                                          ".named { background: radial-gradient(circle, blue, purple); }\n"
                                          ".warm { background: linear-gradient(90deg, #f97316, #db2777); }\n")
        self.assertEqual(self.heads(rel), ["src/ui/hero.css:2: 파랑·보라 그라디언트", "src/ui/hero.css:9: 파랑·보라 그라디언트"])

    def test_tailwind_gradient(self):
        rel = self.put("src/ui/Hero.tsx", '<h1 className="bg-gradient-to-r from-indigo-500 to-violet-600">x</h1>\n'
                                          '<div className="bg-gradient-to-r from-blue-500 to-sky-400">y</div>\n')
        self.assertEqual(self.heads(rel), ["src/ui/Hero.tsx:1: 파랑·보라 그라디언트"])

    def test_skips_excluded_unsupported_missing_large_and_non_utf8(self):
        rels = [self.put("docs/planning/spec.md", "🚀\n"), self.put("docs/design/references.md", "🚀\n"),
                self.put("docs/tasks/T-001/report.md", "🚀\n"), self.put("docs/reviews/T-001/r1.md", "🚀\n"),
                self.put("scripts/run.py", "print('🚀')\n"), "src/deleted.css"]
        self.put("src/big.css", "🚀\n" + "a" * (1 << 20))
        (self.root / "src/bad.css").write_bytes(b"\xff\xfe border-left: 4px solid red;\n")
        self.assertEqual(design_lint(self.root, [*rels, "src/big.css", "src/bad.css"]), [])

    def test_caps_findings(self):
        rel = self.put("docs/design/screens/home/copy.md", "".join(f"{n} 🚀\n" for n in range(MAX_FINDINGS + 10)))
        found = design_lint(self.root, [rel])
        self.assertEqual(len(found), MAX_FINDINGS + 1)
        self.assertEqual(found[-1], "… 외 10건")


if __name__ == "__main__":
    unittest.main()
