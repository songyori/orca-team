import unittest

from helpers import ProjectCase
from orch.designdiff import design_diff_lint

LOST = "지운 줄에서 {name} {n}개가 줄었다(대체가 있는지 확인)"


class DesignDiffTest(ProjectCase):
    def commit(self, rel, text, message="[T-001] feat: ui"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def lint(self, files, *shas):
        return design_diff_lint(self.root, list(files), list(shas))

    def test_removed_aria_label_is_reported(self):
        self.commit("src/Close.tsx", '<button aria-label="닫기"><X /></button>\n<button aria-label="열기"><O /></button>\n',
                    "chore: base")
        sha = self.commit("src/Close.tsx", "<button><X /></button>\n<button><O /></button>\n")
        self.assertEqual(self.lint(["src/Close.tsx"], sha), [f"src/Close.tsx: {LOST.format(name='aria-label', n=2)}"])

    def test_moved_or_rewrapped_signal_is_not_reported(self):
        self.commit("src/Close.tsx", '<button aria-label="닫기" className="a">x</button>\n', "chore: base")
        sha = self.commit("src/Close.tsx", '<button\n  className="b"\n  aria-label="닫기"\n>x</button>\n')
        self.assertEqual(self.lint(["src/Close.tsx"], sha), [])

    def test_labelledby_is_counted_apart_from_label(self):
        self.commit("src/Dialog.tsx", '<div role="dialog" aria-labelledby="t">\n', "chore: base")
        sha = self.commit("src/Dialog.tsx", '<div role="dialog" aria-label="설정">\n')
        self.assertEqual(self.lint(["src/Dialog.tsx"], sha),
                         [f"src/Dialog.tsx: {LOST.format(name='aria-labelledby', n=1)}"])

    def test_motion_and_korean_wrap_signals(self):
        self.commit("src/title.css", ".title { word-break: keep-all; }\n"
                    "@media (prefers-reduced-motion: no-preference) { .a { transition: scale 150ms; } }\n", "chore: base")
        css = self.commit("src/title.css", ".title { }\n.a { transition: scale 150ms; }\n")
        self.commit("src/Title.tsx", '<h1 className="break-keep">제목</h1>\n', "chore: base")
        tsx = self.commit("src/Title.tsx", "<h1>제목</h1>\n")
        self.assertEqual(self.lint(["src/title.css", "src/Title.tsx"], css, tsx), [
            f"src/title.css: {LOST.format(name='prefers-reduced-motion', n=1)}",
            f"src/title.css: {LOST.format(name='keep-all', n=1)}",
            f"src/Title.tsx: {LOST.format(name='break-keep', n=1)}",
        ])

    def test_app_signals(self):
        self.commit("lib/done.dart", 'Text("완료", semanticsLabel: "완료됨")\nIcon(Icons.close, semanticLabel: "닫기")\n',
                    "chore: base")
        dart = self.commit("lib/done.dart", 'Text("완료")\nIcon(Icons.close)\n')
        self.commit("app/Close.kt", 'Image(painter, contentDescription = "닫기")\n', "chore: base")
        kt = self.commit("app/Close.kt", "Image(painter)\n")
        self.assertEqual(self.lint(["lib/done.dart", "app/Close.kt"], dart, kt), [
            f"lib/done.dart: {LOST.format(name='semanticsLabel', n=1)}",
            f"lib/done.dart: {LOST.format(name='semanticLabel', n=1)}",
            f"app/Close.kt: {LOST.format(name='contentDescription', n=1)}",
        ])

    def test_zoom_block_in_added_lines(self):
        html = self.commit("src/index.html", '<meta name="viewport" content="width=device-width, initial-scale=1, '
                                             'maximum-scale=1.0, user-scalable=no">\n')
        ok = self.commit("src/ok.html", '<meta name="viewport" content="width=device-width, maximum-scale=5">\n')
        nxt = self.commit("src/app/layout.tsx", 'export const viewport = { width: "device-width", maximumScale: 1, '
                                                'userScalable: false };\n')
        self.assertEqual(self.lint(["src/index.html", "src/ok.html", "src/app/layout.tsx"], html, ok, nxt), [
            'src/index.html:1: 확대 막기(maximum-scale=1.0, user-scalable=no">)',
            "src/app/layout.tsx:1: 확대 막기(maximumScale: 1, userScalable: false };)",
        ])

    def test_focus_removed_without_focus_visible(self):
        bad = self.commit("src/btn.css", ".btn:focus { outline: none; }\n")
        good = self.commit("src/ok.css", ".btn { outline: none; }\n.btn:focus-visible { outline: 2px solid var(--ring); }\n")
        tw_good = self.commit("src/Ok.tsx", '<button className="outline-none focus-visible:ring-2">x</button>\n'
                                            '<a className="focus:outline-none focus:ring-2">y</a>\n')
        tw_bad = self.commit("src/Bad.tsx", '<button className="focus:outline-none">x</button>\n')
        offset = self.commit("src/offset.css", ".a { outline-offset: 0; }\n")
        files = ["src/btn.css", "src/ok.css", "src/Ok.tsx", "src/Bad.tsx", "src/offset.css"]
        self.assertEqual(self.lint(files, bad, good, tw_good, tw_bad, offset), [
            "src/btn.css:1: 포커스 표시 제거(outline: none; }, 파일에 focus-visible 없음)",
            'src/Bad.tsx:1: 포커스 표시 제거(outline-none">x</button>, 파일에 focus-visible 없음)',
        ])

    def test_only_lines_added_by_given_commits(self):
        self.commit("src/btn.css", ".btn { outline: none; }\n", "chore: base")
        sha = self.commit("src/btn.css", ".btn { outline: none; }\n.x { color: red; }\n")
        self.assertEqual(self.lint(["src/btn.css"], sha), [])

    def test_skips_non_targets_excluded_paths_and_git_errors(self):
        txt = self.commit("notes.txt", "outline: none\n")
        task = self.commit("docs/tasks/T-001/report.md", "outline: none\n")
        self.assertEqual(self.lint(["notes.txt", "docs/tasks/T-001/report.md"], txt, task), [])
        self.assertEqual(self.lint(["src/btn.css"], "0" * 40), [])

    def test_deleted_file_checks_removed_lines_only(self):
        self.commit("src/Icon.tsx", '<button aria-label="닫기">x</button>\n', "chore: base")
        self.git("rm", "-q", "--", "src/Icon.tsx")
        self.git("commit", "-q", "-m", "[T-001] chore: remove")
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.lint(["src/Icon.tsx"], sha), [f"src/Icon.tsx: {LOST.format(name='aria-label', n=1)}"])


if __name__ == "__main__":
    unittest.main()
