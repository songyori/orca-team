import json
import os
import stat
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from helpers import PLUGIN, ProjectCase


class ScaffoldTest(ProjectCase):
    def run_scaffold(self, text="할 일 앱. 웹."):
        desc = self.tmp / "d.txt"
        desc.write_text(text, encoding="utf-8")
        return json.loads(self.orch("scaffold", "--description-file", str(desc)).stdout)

    def test_creates_files_and_setup_mode(self):
        out = self.run_scaffold()
        for rel in (".orchestration/config.toml", ".orchestration/roles/designer.md",
                    ".orchestration/roles/director.md", ".orchestration/design-rules.md",
                    ".orchestration/text-rules.md", ".orchestration/bin/orch", ".orchestration/project.md", ".orchestration/tasks/.gitkeep",
                    "CLAUDE.md", "AGENTS.md", ".gitignore"):
            self.assertTrue((self.root / rel).exists(), rel)
        self.assertIn("할 일 앱. 웹.", (self.root / ".orchestration/project.md").read_text(encoding="utf-8"))
        self.assertIn(".orchestration/state/", (self.root / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertTrue((self.root / ".orchestration/state/setup-mode").exists())
        self.assertTrue(os.stat(self.root / ".orchestration/bin/orch").st_mode & stat.S_IXUSR)
        self.assertEqual(out["skipped"], [])

    def test_idempotent_and_keeps_existing_claude_md(self):
        (self.root / "CLAUDE.md").write_text("# 기존 규칙\n", encoding="utf-8")
        self.run_scaffold()
        out = self.run_scaffold()
        text = (self.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 기존 규칙"))
        self.assertEqual(text.count("<!-- orca-team -->"), 1)
        self.assertIn("CLAUDE.md", out["skipped"])
        self.assertEqual(out["created"], [])

    def test_second_run_keeps_single_section_and_gitignore_line(self):
        self.run_scaffold()
        self.run_scaffold()
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents.count("<!-- orca-team -->"), 1)
        lines = (self.root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines.count(".orchestration/state/"), 1)

    def test_gitignore_without_trailing_newline_keeps_other_lines(self):
        (self.root / ".gitignore").write_text(".env\nnode_modules/", encoding="utf-8")
        self.run_scaffold()
        self.run_scaffold()
        lines = (self.root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, [".env", "node_modules/", ".orchestration/state/"])

    def test_second_run_keeps_user_modified_files(self):
        self.run_scaffold()
        config = self.root / ".orchestration/config.toml"
        role = self.root / ".orchestration/roles/designer.md"
        config.write_text("# 사용자 수정\n", encoding="utf-8")
        role.write_text("# 내 디자이너 역할\n", encoding="utf-8")
        out = self.run_scaffold()
        self.assertEqual(config.read_text(encoding="utf-8"), "# 사용자 수정\n")
        self.assertEqual(role.read_text(encoding="utf-8"), "# 내 디자이너 역할\n")
        self.assertIn(".orchestration/config.toml", out["skipped"])
        self.assertIn(".orchestration/roles/designer.md", out["skipped"])

    def test_setup_done_removes_flag(self):
        self.run_scaffold()
        self.orch("setup-done")
        self.assertFalse((self.root / ".orchestration/state/setup-mode").exists())

    def test_shim_runs_plugin_and_reports_missing(self):
        self.run_scaffold()
        shim = str(self.root / ".orchestration/bin/orch")
        ok = subprocess.run([shim, "--version"], cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertIn("orca-team", ok.stdout)
        env = {k: v for k, v in self.env.items() if k != "ORCA_TEAM_PLUGIN_ROOT"}
        missing = subprocess.run([shim, "--version"], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(missing.returncode, 12)
        registry = {"plugins": {"orca-team@setup-with-orca": [{"scope": "user", "installPath": str(PLUGIN)}]}}
        Path(env["ORCA_TEAM_PLUGINS_JSON"]).write_text(json.dumps(registry), encoding="utf-8")
        found = subprocess.run([shim, "--version"], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertIn("orca-team", found.stdout)


class ScaffoldPluginRootTest(ProjectCase):
    def test_scaffold_records_own_plugin_root_every_time(self):
        marker = self.root / ".orchestration/state/plugin-root"
        self.scaffold()
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), str(PLUGIN.resolve()))
        marker.write_text("/stale\n", encoding="utf-8")
        self.scaffold()
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), str(PLUGIN.resolve()))


class ShimPluginRootTest(ProjectCase):
    """shim 의 플러그인 경로 우선순위: 환경변수 > state/plugin-root > installed_plugins.json."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        self.shim = str(self.root / ".orchestration/bin/orch")
        self.bare = {k: v for k, v in self.env.items() if k != "ORCA_TEAM_PLUGIN_ROOT"}
        self.fake = self.tmp / "fake-plugin"
        (self.fake / "bin").mkdir(parents=True)
        (self.fake / "bin" / "orch").write_text("#!/usr/bin/env python3\nprint('FAKE-ROOT')\n", encoding="utf-8")
        self.marker = self.root / ".orchestration/state/plugin-root"
        self.bare["ORCA_TEAM_KNOWN_MARKETPLACES_JSON"] = str(self.tmp / "known_marketplaces.json")
        self.marker.parent.mkdir(parents=True, exist_ok=True)

    def run_shim(self, env, cwd=None):
        return subprocess.run([self.shim, "--version"], cwd=cwd or self.root, env=env, capture_output=True, text=True)

    def trust_registry(self, path):
        registry = {"plugins": {"orca-team@setup-with-orca": [{"scope": "user", "installPath": str(path)}]}}
        Path(self.bare["ORCA_TEAM_PLUGINS_JSON"]).write_text(json.dumps(registry), encoding="utf-8")

    def test_marker_is_used_without_env(self):
        self.trust_registry(self.fake)
        self.marker.write_text(str(self.fake) + "\n", encoding="utf-8")
        self.assertIn("FAKE-ROOT", self.run_shim(self.bare).stdout)

    def test_marker_found_from_any_cwd(self):
        self.trust_registry(self.fake)
        self.marker.write_text(str(self.fake), encoding="utf-8")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.assertIn("FAKE-ROOT", self.run_shim(self.bare, cwd=elsewhere).stdout)

    def test_env_beats_marker(self):
        self.marker.write_text(str(self.fake), encoding="utf-8")
        proc = self.run_shim(self.env)
        self.assertNotIn("FAKE-ROOT", proc.stdout)
        self.assertIn("orca-team", proc.stdout)

    def test_marker_beats_registry_when_both_trusted(self):
        registry = {"plugins": {"orca-team@setup-with-orca": [
            {"scope": "user", "installPath": str(PLUGIN)}, {"scope": "project", "installPath": str(self.fake)}]}}
        Path(self.bare["ORCA_TEAM_PLUGINS_JSON"]).write_text(json.dumps(registry), encoding="utf-8")
        self.marker.write_text(str(self.fake), encoding="utf-8")
        self.assertIn("FAKE-ROOT", self.run_shim(self.bare).stdout)

    def test_untrusted_marker_is_ignored_and_registry_used(self):
        self.trust_registry(PLUGIN)
        self.marker.write_text(str(self.fake), encoding="utf-8")
        proc = self.run_shim(self.bare)
        self.assertNotIn("FAKE-ROOT", proc.stdout)
        self.assertIn("orca-team", proc.stdout)

    def test_untrusted_marker_without_registry_reports_missing(self):
        self.marker.write_text(str(self.fake), encoding="utf-8")
        self.assertEqual(self.run_shim(self.bare).returncode, 12)

    def test_directory_marketplace_path_is_trusted(self):
        market = self.tmp / "market"
        plugin = market / "orca-team"
        (plugin / "bin").mkdir(parents=True)
        (plugin / "bin" / "orch").write_text("#!/usr/bin/env python3\nprint('MARKET-ROOT')\n", encoding="utf-8")
        known = {"setup-with-orca": {"source": {"source": "directory", "path": str(market)}}}
        Path(self.bare["ORCA_TEAM_KNOWN_MARKETPLACES_JSON"]).write_text(json.dumps(known), encoding="utf-8")
        self.marker.write_text(str(plugin), encoding="utf-8")
        self.assertIn("MARKET-ROOT", self.run_shim(self.bare).stdout)

    def test_non_directory_marketplace_is_not_trusted(self):
        market = self.tmp / "market"
        plugin = market / "orca-team"
        (plugin / "bin").mkdir(parents=True)
        (plugin / "bin" / "orch").write_text("#!/usr/bin/env python3\nprint('MARKET-ROOT')\n", encoding="utf-8")
        known = {"setup-with-orca": {"source": {"source": "github", "path": str(market)}}}
        Path(self.bare["ORCA_TEAM_KNOWN_MARKETPLACES_JSON"]).write_text(json.dumps(known), encoding="utf-8")
        self.marker.write_text(str(plugin), encoding="utf-8")
        self.assertEqual(self.run_shim(self.bare).returncode, 12)

    def test_symlinked_marker_target_must_resolve_to_trusted_path(self):
        link = self.tmp / "link"
        link.symlink_to(self.fake)
        self.trust_registry(self.fake)
        self.marker.write_text(str(link), encoding="utf-8")
        self.assertIn("FAKE-ROOT", self.run_shim(self.bare).stdout)

    def test_stale_marker_falls_back_to_registry(self):
        self.marker.write_text(str(self.tmp / "gone"), encoding="utf-8")
        registry = {"plugins": {"orca-team@setup-with-orca": [{"scope": "user", "installPath": str(PLUGIN)}]}}
        Path(self.bare["ORCA_TEAM_PLUGINS_JSON"]).write_text(json.dumps(registry), encoding="utf-8")
        self.assertIn("orca-team", self.run_shim(self.bare).stdout)

    def make_plugin(self, base, name):
        plugin = base / "orca-team"
        (plugin / "bin").mkdir(parents=True)
        (plugin / "bin" / "orch").write_text(f"#!/usr/bin/env python3\nprint('{name}')\n", encoding="utf-8")
        return plugin

    def test_no_marker_prefers_directory_source_over_install_path(self):
        self.marker.unlink(missing_ok=True)
        market = self.tmp / "market"
        self.make_plugin(market, "DIR-SOURCE")
        known = {"setup-with-orca": {"source": {"source": "directory", "path": str(market)}}}
        Path(self.bare["ORCA_TEAM_KNOWN_MARKETPLACES_JSON"]).write_text(json.dumps(known), encoding="utf-8")
        self.trust_registry(self.fake)
        self.assertIn("DIR-SOURCE", self.run_shim(self.bare).stdout)

    def test_no_marker_falls_back_to_install_path_without_directory_source(self):
        self.marker.unlink(missing_ok=True)
        self.trust_registry(self.fake)
        self.assertIn("FAKE-ROOT", self.run_shim(self.bare).stdout)

    def test_directory_source_without_bin_orch_is_skipped(self):
        self.marker.unlink(missing_ok=True)
        market = self.tmp / "market"
        market.mkdir()
        known = {"setup-with-orca": {"source": {"source": "directory", "path": str(market)}}}
        Path(self.bare["ORCA_TEAM_KNOWN_MARKETPLACES_JSON"]).write_text(json.dumps(known), encoding="utf-8")
        self.trust_registry(self.fake)
        self.assertIn("FAKE-ROOT", self.run_shim(self.bare).stdout)

    def test_stale_marker_without_registry_reports_missing(self):
        self.marker.write_text(str(self.tmp / "gone"), encoding="utf-8")
        self.assertEqual(self.run_shim(self.bare).returncode, 12)


class UpgradeTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")

    def upgrade(self):
        return json.loads(self.orch("upgrade").stdout)

    def template(self, rel):
        return (PLUGIN / "templates" / rel).read_text(encoding="utf-8")

    def commit_text(self, rel, text):
        (self.root / rel).write_text(text, encoding="utf-8")
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", f"chore: {rel}")

    def test_same_files_change_nothing(self):
        out = self.upgrade()
        self.assertEqual((out["updated"], out["created"], out["skipped"], out["diff_stat"]), ([], [], [], ""))
        self.assertIn("/orca-team:up", out["next"])

    def test_overwrites_committed_old_copy_without_commit(self):
        self.commit_text(".orchestration/roles/planner.md", "옛 지침\n")
        head = self.git("rev-parse", "HEAD").strip()
        out = self.upgrade()
        self.assertEqual(out["updated"], [".orchestration/roles/planner.md"])
        self.assertEqual((self.root / ".orchestration/roles/planner.md").read_text(encoding="utf-8"),
                         self.template("roles/planner.md"))
        self.assertIn(".orchestration/roles/planner.md", out["diff_stat"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)

    def test_skips_uncommitted_change(self):
        (self.root / ".orchestration/roles/tester.md").write_text("작업 중\n", encoding="utf-8")
        out = self.upgrade()
        self.assertEqual(out["skipped"], [{"path": ".orchestration/roles/tester.md",
                                           "reason": "커밋 안 된 변경이 있다. 커밋하거나 되돌린 뒤 다시 한다"}])
        self.assertEqual((self.root / ".orchestration/roles/tester.md").read_text(encoding="utf-8"), "작업 중\n")

    def test_diff_stat_only_covers_changed_files(self):
        (self.root / ".orchestration/roles/tester.md").write_text("작업 중\n", encoding="utf-8")
        self.assertEqual(self.upgrade()["diff_stat"], "")
        self.commit_text(".orchestration/roles/planner.md", "옛 지침\n")
        out = self.upgrade()
        self.assertEqual(out["updated"], [".orchestration/roles/planner.md"])
        self.assertIn(".orchestration/roles/planner.md", out["diff_stat"])
        self.assertNotIn("tester.md", out["diff_stat"])

    def test_keeps_config_toml(self):
        self.commit_text(".orchestration/config.toml", self.template("config.toml") + "# 사용자 설정\n")
        out = self.upgrade()
        self.assertTrue((self.root / ".orchestration/config.toml").read_text(encoding="utf-8").endswith("# 사용자 설정\n"))
        self.assertNotIn(".orchestration/config.toml", json.dumps(out, ensure_ascii=False))

    def test_creates_missing_file(self):
        self.git("rm", "-q", "--", ".orchestration/roles/director.md")
        self.git("commit", "-q", "-m", "chore: director 없음")
        out = self.upgrade()
        self.assertEqual(out["created"], [".orchestration/roles/director.md"])

    def test_untracked_file_skipped(self):
        self.git("rm", "-q", "--cached", "--", ".orchestration/text-rules.md")
        self.git("commit", "-q", "-m", "chore: 추적 해제")
        out = self.upgrade()
        self.assertEqual(out["skipped"][0]["path"], ".orchestration/text-rules.md")
        self.assertIn("추적되지 않는다", out["skipped"][0]["reason"])


OLD_CONFIG = """max_parallel = 4
user_approval_roles = ["designer"]

[coordinator]
model = "opus"
effort = "xhigh"
auto_up = false

[review]
max_rounds = 3

[defaults]
claude_model = "sonnet"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "high"

[roles.planner]
title = "기획"
codex_effort = "high"
owns = ["docs/planning/**"]

[roles.designer]
title = "디자인"
codex_effort = "medium"
owns = ["docs/design/**"]

[roles.developer]
title = "개발"
codex_effort = "low"
owns = ["src/**"]
"""


class Upgrade070Test(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        (self.root / ".orchestration/config.toml").write_text(OLD_CONFIG, encoding="utf-8")
        (self.root / ".orchestration/coordinator.md").write_text("# 옛 코디네이터 절차\n", encoding="utf-8")
        claude = self.root / "CLAUDE.md"
        claude.write_text("# 내 프로젝트\n\n<!-- orca-team -->\n## orca-team 오케스트레이션\n옛 메인 규칙(orch tell)\n",
                          encoding="utf-8")
        (self.root / ".orchestration/state/run.json").write_text(
            json.dumps({"run_id": "run_old", "coordinator_handle": "term_old"}), encoding="utf-8")
        self.respond("terminal show", {"ok": False, "error": {"code": "terminal_handle_stale"}}, code=1)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "0.6.0 project")

    def upgrade(self, check=True):
        return self.orch("upgrade", check=check, env={"ORCA_TERMINAL_HANDLE": "term_main"})

    def test_migrates_coordinator_project(self):
        out = json.loads(self.upgrade().stdout)
        self.assertIn(".orchestration/coordinator.md", out["removed"])
        self.assertFalse((self.root / ".orchestration/coordinator.md").exists())
        cfg = tomllib.loads((self.root / ".orchestration/config.toml").read_text(encoding="utf-8"))
        self.assertNotIn("coordinator", cfg)
        self.assertIs(cfg["drive"]["auto_up"], False)
        self.assertIs(cfg["overflow"]["enabled"], True)
        self.assertEqual(cfg["agents"]["production"]["roles"], ["planner", "designer", "developer"])
        self.assertNotIn("research", cfg["agents"])  # director 직무가 없는 프로젝트
        self.assertEqual((cfg["defaults"]["codex_effort"], cfg["roles"]["planner"]["codex_effort"],
                          cfg["roles"]["designer"]["codex_effort"], cfg["roles"]["developer"]["codex_effort"]),
                         ("xhigh", "xhigh", "xhigh", "low"))  # 사용자가 바꾼 low 는 둔다
        claude = (self.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude.startswith("# 내 프로젝트\n"))
        self.assertIn("### 판단 기준 (decide)", claude)
        self.assertNotIn("orch tell", claude)
        self.assertNotIn("coordinator_handle", json.loads((self.root / ".orchestration/state/run.json").read_text()))
        self.assertIn("/orca-team:up", out["next"])

    def test_refuses_while_tasks_run_or_coordinator_lives(self):
        state = self.root / ".orchestration/state/tasks/T-001.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"id": "T-001", "status": "working"}), encoding="utf-8")
        proc = self.upgrade(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(working)", proc.stderr)
        state.unlink()
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}})
        proc = self.upgrade(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("코디네이터 탭을 닫은 뒤", proc.stderr)

    def test_force_upgrades_with_reviewing_task_but_not_with_live_coordinator(self):
        state = self.root / ".orchestration/state/tasks/T-001.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"id": "T-001", "status": "reviewing"}), encoding="utf-8")
        proc = self.upgrade(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--force", proc.stderr)
        self.assertIn("run_id", proc.stderr)
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}})
        proc = self.orch("upgrade", "--force", check=False, env={"ORCA_TERMINAL_HANDLE": "term_main"})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("코디네이터 탭을 닫은 뒤", proc.stderr)
        self.respond("terminal show", {"ok": False, "error": {"code": "terminal_handle_stale"}}, code=1)
        out = json.loads(self.orch("upgrade", "--force", env={"ORCA_TERMINAL_HANDLE": "term_main"}).stdout)
        self.assertIn(".orchestration/coordinator.md", out["removed"])
        self.assertEqual(json.loads(state.read_text())["status"], "reviewing")
        run = json.loads((self.root / ".orchestration/state/run.json").read_text())
        self.assertEqual(run["run_id"], "run_old")

    def test_next_says_new_session_before_up(self):
        nxt = json.loads(self.upgrade().stdout)["next"]
        self.assertIn("/clear", nxt)
        self.assertLess(nxt.index("/clear"), nxt.index("/orca-team:up"))

    def test_skipped_config_next_says_fix_config(self):
        (self.root / ".orchestration/config.toml").write_text(OLD_CONFIG + "\n# 고치는 중\n", encoding="utf-8")
        out = json.loads(self.upgrade().stdout)
        self.assertIn("config.toml", out["next"])
        self.assertIn("다시", out["next"])
        self.assertNotIn("/orca-team:up", out["next"])

    def test_no_op_effort_change_is_not_reported(self):
        text = OLD_CONFIG.replace('codex_effort = "low"', 'codex_effort = "xhigh"')
        self.commit_config(text)
        out = json.loads(self.upgrade().stdout)
        self.assertFalse([c for c in out["config"]["changed"] if "xhigh → xhigh" in c], out["config"])

    def test_dirty_config_is_skipped(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(OLD_CONFIG + "\n# 고치는 중\n", encoding="utf-8")
        out = json.loads(self.upgrade().stdout)
        self.assertIn("커밋 안 된 변경", out["config"]["skipped"])
        self.assertIn("[coordinator]", path.read_text(encoding="utf-8"))

    def test_current_config_is_left_alone(self):
        path = self.root / ".orchestration/config.toml"
        text = OLD_CONFIG.replace("[coordinator]", "[drive]").replace('model = "opus"\neffort = "xhigh"\n', "")
        path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "0.7.0 config")
        before = path.read_bytes()
        out = json.loads(self.upgrade().stdout)
        self.assertEqual(out["config"], {"changed": []})
        self.assertEqual(path.read_bytes(), before)

    def commit_config(self, text):
        (self.root / ".orchestration/config.toml").write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "config")

    def test_keeps_comments_of_other_tables(self):
        text = OLD_CONFIG.replace("[coordinator]", "# 코디네이터 설정\n[coordinator] # 주석").replace(
            "[review]", "# 리뷰 설정\n[review]").replace("[defaults]", "\n# 기본값\n[defaults]").replace(
            "[roles.designer]", "# 디자인 직무\n[roles.designer]")
        self.commit_config(text)
        self.upgrade()
        after = (self.root / ".orchestration/config.toml").read_text(encoding="utf-8")
        for keep in ("# 리뷰 설정\n[review]", "# 기본값\n[defaults]", "# 디자인 직무\n[roles.designer]"):
            self.assertIn(keep, after)
        self.assertNotIn("[coordinator]", after)
        self.assertIn('codex_effort = "xhigh"', after)
        self.assertIn('[roles.designer]\ntitle = "디자인"\ncodex_effort = "xhigh"', after)

    def test_migrated_config_loads(self):
        self.upgrade()
        sys.path.insert(0, str(PLUGIN / "lib"))
        try:
            from orch.config import Config
            Config.load(self.root / ".orchestration/config.toml")
        finally:
            sys.path.remove(str(PLUGIN / "lib"))

    def test_keeps_drive_handle(self):
        run = self.root / ".orchestration/state/run.json"
        run.write_text(json.dumps({"run_id": "r", "coordinator_handle": "term_old", "drive_handle": "term_drive"}),
                       encoding="utf-8")
        self.upgrade()
        self.assertEqual(json.loads(run.read_text())["drive_handle"], "term_drive")

    def test_no_run_json_is_not_created(self):
        run = self.root / ".orchestration/state/run.json"
        run.unlink()
        self.upgrade()
        self.assertFalse(run.exists())

    def test_director_gets_research_agent(self):
        self.commit_config(OLD_CONFIG + '\n[roles.director]\ntitle = "감독"\nowns = ["docs/direction/**"]\n')
        self.upgrade()
        cfg = tomllib.loads((self.root / ".orchestration/config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["agents"]["research"]["roles"], ["director"])

    def test_unremovable_coordinator_fails_clearly(self):
        text = OLD_CONFIG.replace("[coordinator]\n", "").replace("max_parallel = 4\n", "max_parallel = 4\ncoordinator = { auto_up = false }\n")
        self.commit_config(text)
        out = json.loads(self.upgrade().stdout)
        self.assertIn("coordinator", out["config"]["skipped"])
        self.assertEqual((self.root / ".orchestration/config.toml").read_text(encoding="utf-8"), text)

    def test_unmatched_codex_effort_is_not_recorded(self):
        text = OLD_CONFIG.replace('codex_effort = "high"\n\n[roles.planner]', "codex_effort = { v = 1 }\n\n[roles.planner]", 1)
        text = text.replace('[roles.planner]\ntitle = "기획"\ncodex_effort = "high"', '[roles.planner]\ntitle = "기획"\ncodex_effort = \'high\'')
        self.commit_config(text)
        out = json.loads(self.upgrade().stdout)
        self.assertFalse([c for c in out["config"]["changed"] if "roles.planner" in c], out["config"])


class DoctorTest(ProjectCase):
    def ready(self):
        self.respond("status", {"ok": True, "result": {"runtime": {"state": "ready"}}})

    def test_all_ok(self):
        self.ready()
        proc = self.orch("doctor", check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("FAIL", proc.stdout)

    def runner(self, text: str, mode: int = 0o755) -> Path:
        path = self.tmp / "runner.sh"
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_embedded_wrapper_is_default(self):
        self.ready()
        env = {k: v for k, v in self.env.items() if k != "ORCA_TEAM_CODEX_RUN"}
        proc = subprocess.run([sys.executable, str(PLUGIN / "bin/orch"), "doctor"], cwd=self.root, env=env,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn(f"ok   Codex 래퍼: {PLUGIN / 'scripts/codex-run.sh'}", proc.stdout)

    def test_runner_without_hook_trust_flag_fails(self):
        self.ready()
        old = self.runner("#!/bin/sh\necho old\n")
        proc = self.orch("doctor", check=False, env={"ORCA_TEAM_CODEX_RUN": str(old)})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL Codex 래퍼", proc.stdout)
        self.assertIn("--bypass-hook-trust", proc.stdout)

    def test_non_executable_runner_fails(self):
        self.ready()
        runner = self.runner("#!/bin/sh\n# --bypass-hook-trust\n", mode=0o644)
        proc = self.orch("doctor", check=False, env={"ORCA_TEAM_CODEX_RUN": str(runner)})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL Codex 래퍼", proc.stdout)
        self.assertIn("실행 권한", proc.stdout)

    def test_missing_runner_fails_as_broken_install(self):
        self.ready()
        proc = self.orch("doctor", check=False, env={"ORCA_TEAM_CODEX_RUN": str(self.tmp / "gone.sh")})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAIL Codex 래퍼", proc.stdout)
        self.assertIn("플러그인 설치가 깨졌다", proc.stdout)

    def test_image_generation_enabled_is_ok(self):
        self.ready()
        proc = self.orch("doctor", check=False)
        self.assertIn("ok   codex image_generation", proc.stdout)

    def test_image_generation_disabled_is_warning(self):
        self.ready()
        proc = self.orch("doctor", check=False, env={"FAKE_CODEX_IMAGE_GENERATION": "false"})
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("warn codex image_generation", proc.stdout)
        self.assertIn("codex features enable image_generation", proc.stdout)

    def test_image_generation_missing_is_warning(self):
        self.ready()
        proc = self.orch("doctor", check=False, env={"FAKE_CODEX_IMAGE_GENERATION": "absent"})
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("warn codex image_generation", proc.stdout)

    def test_runtime_not_ready_fails(self):
        self.respond("status", {"ok": True, "result": {"runtime": {"state": "starting"}}})
        proc = self.orch("doctor", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("runtime.state=starting", proc.stdout)

    def test_missing_git_repo_is_warning(self):
        self.ready()
        bare = self.tmp / "nogit"
        bare.mkdir()
        proc = subprocess.run([str(PLUGIN / "bin/orch"), "doctor"], cwd=bare, env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("warn git 저장소", proc.stdout)


if __name__ == "__main__":
    unittest.main()
