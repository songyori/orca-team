import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from orch.textlint import MAX_BYTES, ending_styles, load_glossary, scan_text, text_targets

VOICE = """# 문구 기준

## 문체
- 서비스 문구: 해요체

## 용어집
| 쓰는 표기 | 쓰지 않는 표기 | 메모 |
|---|---|---|
| 로그인 | 로그 인, 로긴 | |
| 회원가입 | `회원 가입` | |
| 장바구니 |  | 금지 표기 없음 |

## 다음 절
| 쓰는 표기 | 쓰지 않는 표기 | 메모 |
|---|---|---|
| 무시 | 무시됨 | |
"""

CASES = [
    ('오탈자 "됬"', "결제가 완료됬어요"),
    ('오탈자 "안되요"', "지금은 안되요"),
    ('이중피동 "되어진"', "설정이 저장되어진 상태예요"),
    ('이중피동 "되어졌"', "오류가 발생되어졌어요"),
    ('번역투 "에 있어서"', "보안에 있어서 중요해요"),
    ('상투어 "혁신적"', "혁신적인 경험을 드려요"),
    ('상투어 "Seamless"', "Seamless 결제 경험"),
    ('hedging "라고 할 수 있"', "가장 빠른 방법이라고 할 수 있어요"),
    ('챗봇 잔재 "물론입니다"', "물론입니다. 바로 도와드릴게요"),
    ('쉼표 "그리고,"', "그리고, 저장 버튼을 눌러요"),
    ('쉼표 "며,"', "주문을 확인하며, 결제를 진행해요"),
    ('쉼표 "해서,"', "자주 사용해서, 바로가기에 넣었어요"),
]


class ScanTextTest(unittest.TestCase):
    def scan(self, text, rel="src/app.tsx", glossary=()):
        return scan_text(rel, text, None, list(glossary))

    def test_each_category(self):
        for label, line in CASES:
            with self.subTest(label=label):
                found = self.scan(f'const a = "{line}";\n')
                self.assertTrue(any(f.startswith(f"src/app.tsx:1: {label}(") for f in found), found)

    def test_finding_format(self):
        self.assertEqual(self.scan('const a = "결제가 완료됬어요";\n'),
                         ['src/app.tsx:1: 오탈자 "됬"(const a = "결제가 완료됬어요";)'])

    def test_does_not_flag_normal_text(self):
        for line in ("회원님은 언제든지 탈퇴할 수 있습니다", "결제는 카드를 통해 진행해요", "unlock()", "로그인이 필요"):
            with self.subTest(line=line):
                self.assertEqual(self.scan(f'const a = "{line}";\n'), [])

    def test_skips_code_comments(self):
        self.assertEqual(self.scan("// 완료됬어요\n  * 완료됬어요\n/* 완료됬어요 */\n"), [])
        self.assertEqual(self.scan("# 완료됬어요\n", rel="src/i18n/ko.yaml"), [])
        self.assertEqual(self.scan("<!-- 완료됬어요 -->\n", rel="docs/design/screens/home.html"), [])

    def test_markdown_skips_code_but_not_bullets(self):
        md = "```\n완료됬어요\n```\n`완료됬어요` 는 예시예요\n"
        self.assertEqual(self.scan(md, rel="docs/user/guide.md"), [])
        self.assertEqual(len(self.scan("* 완료됬어요\n", rel="docs/user/guide.md")), 1)

    def test_added_filter(self):
        text = 'const a = "완료됬어요";\nconst b = "또 완료됬어요";\n'
        found = scan_text("src/app.tsx", text, {'const b = "또 완료됬어요";'})
        self.assertEqual([f.split(":")[1] for f in found], ["2"])

    def test_banned_term_inside_preferred_is_skipped(self):
        glossary = [("회원가입", ["가입"])]
        self.assertEqual(self.scan('const a = "회원가입 완료";\n', glossary=glossary), [])
        self.assertEqual(self.scan('const a = "가입 완료";\n', glossary=glossary),
                         ['src/app.tsx:1: 금지 표기 "가입"→"회원가입"(const a = "가입 완료";)'])

    def test_mixed_endings_reported_once_with_minor_lines(self):
        text = "# 안내\n\n저장했어요.\n\n삭제합니다.\n\n복원합니다.\n"
        found = self.scan(text, rel="docs/user/guide.md")
        self.assertEqual(found, ["docs/user/guide.md:3: 높임법 섞임(해요체 1줄, 하십시오체 2줄. 해요체 줄: 3)"])

    def test_mixed_endings_tie_reports_hasipsio_lines(self):
        found = self.scan("저장했어요.\n삭제합니다.\n", rel="docs/user/guide.md")
        self.assertEqual(found, ["docs/user/guide.md:2: 높임법 섞임(해요체 1줄, 하십시오체 1줄. 하십시오체 줄: 2)"])

    def test_long_line_snippet_is_cut(self):
        line = "가" * 100 + "됬"
        found = self.scan(f"{line}\n", rel="docs/user/guide.md")
        snippet = found[0][found[0].index("(") + 1:-1]
        self.assertEqual(len(snippet), 60)
        self.assertTrue(snippet.endswith("…"))


class EndingStylesTest(unittest.TestCase):
    def test_styles(self):
        self.assertEqual(ending_styles('"저장했어요",'), {"해요체"})
        self.assertEqual(ending_styles("<p>확인하십시오</p>"), {"하십시오체"})
        self.assertEqual(ending_styles("| 저장 | 저장했습니다 |"), {"하십시오체"})
        self.assertEqual(ending_styles("로그인이 필요"), set())
        self.assertEqual(ending_styles("다음 단계로 가요. 저장합니다."), {"하십시오체"})


class GlossaryAndTargetTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orca-team-textlint-")).resolve()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")

    def test_load_glossary(self):
        self.put("voice.md", VOICE)
        self.assertEqual(load_glossary(self.root / "voice.md"),
                         [("로그인", ["로그 인", "로긴"]), ("회원가입", ["회원 가입"])])

    def test_load_glossary_bad_or_missing(self):
        self.assertEqual(load_glossary(self.root / "없음.md"), [])
        self.put("a.md", "# 문구 기준\n\n## 문체\n- 해요체\n")
        self.assertEqual(load_glossary(self.root / "a.md"), [])
        self.put("b.md", "## 용어집\n| 표기 | 금지 |\n|---|---|\n| 로그인 | 로그 인 |\n")
        self.assertEqual(load_glossary(self.root / "b.md"), [])
        self.put("c.md", b"## \xff\n")
        self.assertEqual(load_glossary(self.root / "c.md"), [])

    def test_text_targets(self):
        self.put("src/a.tsx", 'const a = "저장";\n')
        self.put("src/b.tsx", "const b = 1;\n")
        self.put("docs/planning/spec.md", "# 기획\n")
        self.put("docs/design/direction.md", "# 방향\n")
        self.put("docs/design/system/README.md", "# 시스템\n")
        self.put("docs/design/screens/home.html", "<p>홈</p>\n")
        self.put("tests/a.test.ts", 'it("저장");\n')
        self.put("src/logo.png", "로고")
        self.put("src/big.md", "가" * (MAX_BYTES // 3 + 1))
        self.put("src/latin1.md", b"\xff\xfe")
        files = ["src/a.tsx", "src/b.tsx", "docs/planning/spec.md", "docs/design/direction.md",
                 "docs/design/system/README.md", "docs/design/screens/home.html", "tests/a.test.ts", "src/logo.png",
                 "src/big.md", "src/latin1.md", "src/missing.tsx"]
        self.assertEqual(text_targets(self.root, files), ["src/a.tsx", "docs/design/screens/home.html"])


if __name__ == "__main__":
    unittest.main()
