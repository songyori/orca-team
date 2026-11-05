import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from helpers import ProjectCase
from orch.textlint import (MAX_BYTES, MAX_FINDINGS, added_lines, ending_styles, load_glossary, scan_text, text_lint,
                           text_targets)

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
    ('쉼표 "고,"', "저장하고, 닫아요"),
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
        for line in ("회원님은 언제든지 탈퇴할 수 있습니다", "결제는 카드를 통해 진행해요", "unlock()", "로그인이 필요",
                     "신고, 차단, 숨기기", "경고, 오류, 정보", "참고, 아래 표"):
            with self.subTest(line=line):
                self.assertEqual(self.scan(f'const a = "{line}";\n'), [])

    def test_identifier_keys_are_not_cliches(self):
        self.assertEqual(self.scan('{"lock.unlock": "잠금 해제"}\n', rel="src/i18n/ko.json"), [])

    def test_mobile_formats(self):
        self.assertEqual(self.scan("// 완료됬어요\n", rel="ios/App/Foo.swift"), [])
        self.assertEqual(self.scan("<!-- 완료됬어요 -->\n", rel="android/app/src/main/res/values-ko/strings.xml"), [])
        self.assertEqual(self.scan("/* 완료됬어요 */\n", rel="ios/App/ko.lproj/Localizable.strings"), [])
        self.assertEqual(self.scan("// 완료됬어요\n", rel="lib/main.dart"), [])
        self.assertEqual(len(self.scan('"saved": "완료됬어요"\n', rel="lib/l10n/app_ko.arb")), 1)

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

    def test_banned_term_inside_other_rows_preferred_is_skipped(self):
        glossary = [("오늘할일", ["오늘 할일"]), ("할 일", ["할일"])]
        self.assertEqual(self.scan('const t = "오늘할일에 오신 걸 환영해요";\n', glossary=glossary), [])
        self.assertEqual(self.scan('const t = "할일 추가";\n', glossary=glossary),
                         ['src/app.tsx:1: 금지 표기 "할일"→"할 일"(const t = "할일 추가";)'])

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
        self.assertEqual(ending_styles('"아니요"'), set())
        self.assertEqual(ending_styles('"좋아요"'), set())
        self.assertEqual(ending_styles('<string name="saved">저장했어요</string>'), {"해요체"})


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

    def test_load_glossary_strips_trailing_parenthetical(self):
        self.put("voice.md", "## 용어집\n\n| 쓰는 표기 | 쓰지 않는 표기 | 메모 |\n|---|---|---|\n"
                 "| 삭제 | 제거, 지우기(라벨) | |\n| 오늘할일 | 오늘 할 일(서비스 이름으로) | |\n")
        self.assertEqual(load_glossary(self.root / "voice.md"),
                         [("삭제", ["제거", "지우기"]), ("오늘할일", ["오늘 할 일"])])

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
        self.put("src/a.test.tsx", 'it("저장");\n')
        self.put("src/__tests__/a.tsx", 'it("저장");\n')
        self.put("src/logo.png", "로고")
        self.put("lib/l10n/app_ko.arb", '{"a": "저장"}\n')
        self.put("ios/App/ko.lproj/Localizable.strings", '"a" = "저장";\n')
        self.put("android/app/src/main/res/values-ko/strings.xml", "<string>저장</string>\n")
        self.put("src/big.md", "가" * (MAX_BYTES // 3 + 1))
        self.put("src/latin1.md", b"\xff\xfe")
        files = ["src/a.tsx", "src/b.tsx", "docs/planning/spec.md", "docs/design/direction.md",
                 "docs/design/system/README.md", "docs/design/screens/home.html", "tests/a.test.ts", "src/logo.png",
                 "src/big.md", "src/latin1.md", "src/missing.tsx", "src/a.test.tsx", "src/__tests__/a.tsx",
                 "lib/l10n/app_ko.arb", "ios/App/ko.lproj/Localizable.strings",
                 "android/app/src/main/res/values-ko/strings.xml"]
        self.assertEqual(text_targets(self.root, files),
                         ["src/a.tsx", "docs/design/screens/home.html", "lib/l10n/app_ko.arb",
                          "ios/App/ko.lproj/Localizable.strings", "android/app/src/main/res/values-ko/strings.xml"])


class TextLintGitTest(ProjectCase):
    """태스크 커밋이 추가한 줄만 본다. 같은 체크아웃의 다른 커밋과 기존 줄은 잡지 않는다."""

    def commit(self, rel, text, message):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def test_only_lines_added_by_task_commits(self):
        self.commit("src/app.tsx", 'const a = "기존 문구가 완료됬어요";\n', "chore: base")
        sha = self.commit("src/app.tsx", 'const a = "기존 문구가 완료됬어요";\nconst b = "새 문구도 완료됬어요";\n',
                          "[T-001] feat")
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], [sha]),
                         ['src/app.tsx:2: 오탈자 "됬"(const b = "새 문구도 완료됬어요";)'])

    def test_lines_from_other_commits_are_ignored(self):
        sha = self.commit("src/app.tsx", 'const a = "첫 문구";\n', "[T-001] feat")
        self.commit("src/app.tsx", 'const a = "첫 문구";\nconst b = "남의 문구가 완료됬어요";\n', "[T-002] feat")
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], [sha]), [])

    def test_content_line_starting_with_plus_is_kept(self):
        sha = self.commit("docs/user/guide.md", "++ 완료됬어요\n", "[T-001] docs")
        self.assertEqual(added_lines(self.root, [sha], "docs/user/guide.md"), {"++ 완료됬어요"})

    def test_git_error_skips_file(self):
        self.commit("src/app.tsx", 'const a = "완료됬어요";\n', "[T-001] feat")
        self.assertIsNone(added_lines(self.root, ["0" * 40], "src/app.tsx"))
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], ["0" * 40]), [])

    def test_literal_pathspec(self):
        sha = self.commit("src/app/i/page.tsx", 'const a = "완료됬어요";\n', "[T-001] feat")
        self.assertEqual(added_lines(self.root, [sha], "src/app/[id]/page.tsx"), set())

    def test_non_utf8_diff_skips_file_only(self):
        path = self.root / "docs/user/old.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes("완료됬어요\n".encode("cp949"))
        self.git("add", "--", "docs/user/old.md")
        self.git("commit", "-q", "-m", "chore: cp949")
        self.commit("docs/user/old.md", "완료됬어요\n", "[T-001] docs: utf8")
        sha1 = self.git("rev-parse", "HEAD").strip()
        sha2 = self.commit("docs/user/new.md", "새 문서가 완료됬어요\n", "[T-001] docs: new")
        found = text_lint(self.root, ["docs/user/old.md", "docs/user/new.md"], [sha1, sha2])
        self.assertEqual(found, ['docs/user/new.md:1: 오탈자 "됬"(새 문서가 완료됬어요)'])

    def test_glossary_from_voice_md(self):
        self.commit("docs/planning/voice.md", VOICE, "chore: voice")
        sha = self.commit("src/app.tsx", 'const a = "로그 인 해 주세요";\nconst b = "로그인 해 주세요";\n', "[T-001] feat")
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], [sha]),
                         ['src/app.tsx:1: 금지 표기 "로그 인"→"로그인"(const a = "로그 인 해 주세요";)'])

    def test_non_targets_are_skipped(self):
        sha = self.commit("tests/app.test.ts", 'it("완료됬어요");\n', "[T-001] test")
        self.assertEqual(text_lint(self.root, ["tests/app.test.ts"], [sha]), [])

    def test_cap(self):
        lines = "".join(f'const a{i} = "완료됬어요";\n' for i in range(MAX_FINDINGS + 3))
        sha = self.commit("src/app.tsx", lines, "[T-001] feat")
        found = text_lint(self.root, ["src/app.tsx"], [sha])
        self.assertEqual(len(found), MAX_FINDINGS + 1)
        self.assertEqual(found[-1], "… 외 3건")


if __name__ == "__main__":
    unittest.main()
