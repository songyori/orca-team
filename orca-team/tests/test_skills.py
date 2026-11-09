import re
import unittest

from helpers import PLUGIN
from orch.cli import build_parser

# templates/coordinator.md 와 templates/CLAUDE.section.md 는 아직 없어진 orch tell·wait 를 적는다. Task 14 가 새로 쓸 때
# 이 목록에 CLAUDE.section.md 를 다시 넣는다(coordinator.md 는 그때 지운다).
DOCS = [PLUGIN / "skills/init/SKILL.md", PLUGIN / "skills/up/SKILL.md",
        PLUGIN / "templates/roles/planner.md"]


class SkillDocsTest(unittest.TestCase):
    def test_documented_orch_commands_exist(self):
        sub = next(a for a in build_parser()._actions if getattr(a, "dest", None) == "cmd")
        known = set(sub.choices)
        for doc in DOCS:
            for cmd in re.findall(r"\borch ([a-z][a-z-]+)", doc.read_text(encoding="utf-8"), re.I):
                self.assertIn(cmd.lower(), known, f"{doc.name}: orch {cmd}")

    def test_frontmatter(self):
        for name in ("init", "up", "image"):
            text = (PLUGIN / f"skills/{name}/SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"---\nname: {name}\n"), name)
            self.assertIn("description:", text.split("---")[1])

    def test_image_skill_uses_embedded_wrapper(self):
        """/orca-team:image 는 플러그인 안의 래퍼·템플릿만 쓴다."""
        text = (PLUGIN / "skills/image/SKILL.md").read_text(encoding="utf-8")
        for key in ("/orca-team:image", '"${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh" --profile image',
                    "${CLAUDE_PLUGIN_ROOT}/templates/image-prompt.md",
                    '${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/runs/', "codex features list",
                    "turn.completed", "turn.failed", "terminal_event"):
            self.assertIn(key, text)
        self.assertNotIn("--bypass-hook-trust", text)
        prompt = (PLUGIN / "templates/image-prompt.md").read_text(encoding="utf-8")
        for key in ("{prompt}", "{ar}", "{transparent}", "{ref}", "image_gen", "절대경로를 마지막 줄"):
            self.assertIn(key, prompt)

    def test_up_wait_run_gets_long_bash_timeout(self):
        for name in ("init", "up"):
            lines = [line for line in (PLUGIN / f"skills/{name}/SKILL.md").read_text(encoding="utf-8").splitlines()
                     if "orch up --wait-run 180" in line]
            self.assertTrue(lines, name)
            for line in lines:
                self.assertIn("timeout: 240000", line, name)

    def test_up_reasks_open_questions(self):
        text = (PLUGIN / "skills/up/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("open_questions", text)
        self.assertIn(".orchestration/bin/orch reask", text)
        self.assertIn("질문 표시 규칙", text)

    def test_init_uses_fixed_steps(self):
        text = (PLUGIN / "skills/init/SKILL.md").read_text(encoding="utf-8")
        for key in ("ORCH doctor", "ORCH scaffold", "AskUserQuestion", "orch up --wait-run 180",
                    "run_in_background: true", "orch watch", "orch setup-done"):
            self.assertIn(key, text)


if __name__ == "__main__":
    unittest.main()
