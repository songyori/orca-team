import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

from helpers import ProjectCase, fake_test_commands, write_test_config
from orch import orca, tasks
from orch.config import Config
from orch.paths import Project

SPEC = """---
id: {id}
role: {role}
title: {title}
{extra}---
## Target
src/login

## Change
로그인 폼

## Constraints
기존 API 유지

## Acceptance
npm test 통과
"""
STARTED = {"ok": True, "result": {"taskId": "task_1", "dispatchId": "ctx_1",
                                  "launch": {"effective": {"agent": "claude", "model": "opus", "effort": "xhigh"}}}}



def show_worker(case, verdict, stage=None, state=None):
    """실제 Orca 1.4.240 worker-show 모양: worker.stage·state, projection.liveness.verdict."""
    worker = {"agentTerminalHandle": "term_w1"}
    if stage:
        worker["stage"] = stage
    if state:
        worker["state"] = state
    case.respond("orchestration worker-show", {"ok": True, "result": {
        "worker": worker, "projection": {"liveness": {"verdict": verdict}}}})

class TaskCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.respond("orchestration worker-start", STARTED)

    def write_spec(self, task_id, role="developer", title="로그인", extra=""):
        path = self.root / f".orchestration/tasks/{task_id}.md"
        path.write_text(SPEC.format(id=task_id, role=role, title=title, extra=extra), encoding="utf-8")

    def state(self, task_id):
        return json.loads((self.root / f".orchestration/state/tasks/{task_id}.json").read_text(encoding="utf-8"))

    def set_state(self, task_id, **fields):
        st = self.state(task_id)
        st.update(fields)
        (self.root / f".orchestration/state/tasks/{task_id}.json").write_text(json.dumps(st), encoding="utf-8")

    def last_start(self):
        return self.calls_for("orchestration worker-start")[-1]

    @staticmethod
    def arg(call, flag):
        return call[call.index(flag) + 1]


class TaskNewTest(TaskCase):
    def test_numbers_sequentially_from_template(self):
        a = json.loads(self.orch("task", "new", "planner", "요구사항").stdout)
        b = json.loads(self.orch("task", "new", "developer", "구현").stdout)
        self.assertEqual((a["id"], b["id"]), ("T-001", "T-002"))
        text = (self.root / ".orchestration/tasks/T-002.md").read_text(encoding="utf-8")
        self.assertIn("role: developer", text)
        self.assertIn("title: 구현", text)

    def test_rejects_unknown_role(self):
        self.assertEqual(self.orch("task", "new", "chef", "x", check=False).returncode, 2)


class StartTest(TaskCase):
    def test_fixed_placement_and_role_models(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        call = self.last_start()
        self.assertEqual(self.arg(call, "--worktree"), "current")
        self.assertEqual(self.arg(call, "--agent"), "claude")
        self.assertEqual(self.arg(call, "--model"), "opus")
        self.assertEqual(self.arg(call, "--effort"), "xhigh")
        spec = self.arg(call, "--spec")
        for text in ("developer", "src/**", "[T-001]", "로그인 폼", "worker_done"):
            self.assertIn(text, spec)
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["dispatch_id"], st["owns"]), ("working", 1, "ctx_1", ["src/**", "README.md", "docs/user/**", "docs/policy/**"]))

    def test_front_matter_and_flag_override_models(self):
        self.write_spec("T-001", extra="claude_model: fable\n")
        self.orch("start", "T-001", "--effort", "max")
        call = self.last_start()
        self.assertEqual((self.arg(call, "--model"), self.arg(call, "--effort")), ("fable", "max"))

    def test_rejects_placeholder_spec(self):
        self.orch("task", "new", "developer", "x")
        proc = self.orch("start", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("## Target 비었음", proc.stderr)

    def test_refuses_overlapping_owns(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        self.write_spec("T-002", extra="owns: src/ui/**\n")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("T-001", proc.stderr)
        self.write_spec("T-003", role="tester")
        self.orch("start", "T-003")

    def test_launch_failure_needs_retry_flag(self):
        self.respond("orchestration worker-start", {"ok": False, "error": {"code": "x"}}, code=1)
        self.write_spec("T-001")
        proc = self.orch("start", "T-001", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--retry", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "launch_failed")
        self.assertEqual(self.orch("start", "T-001", check=False).returncode, 2)
        self.respond("orchestration worker-start", STARTED)
        self.orch("start", "T-001", "--retry")
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_main_without_drive_is_refused(self):
        self.write_spec("T-001")
        proc = self.orch("start", "T-001", check=False, env={"ORCA_TERMINAL_HANDLE": "term_main"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("/orca-team:up", proc.stderr)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])


class ComposeSpecTest(TaskCase):
    def test_work_stage_has_no_fix_block_or_stray_blank_run(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        spec = self.arg(self.last_start(), "--spec")
        self.assertNotIn("## 수정 라운드", spec)
        self.assertNotIn("\n\n\n", spec)
        self.assertIn("묻는다.\n\n## 작업 규칙\n", spec)

    def test_fix_stage_block_present_and_separated(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        self.set_state("T-001", status="changes")
        self.orch("fix", "T-001", "--note", "P1 반영")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("## 수정 라운드\n", spec)
        self.assertIn("코디네이터 지시: P1 반영\n\n## 작업 규칙\n", spec)
        self.assertNotIn("\n\n\n", spec)
        lines = spec.splitlines()
        i = lines.index("## 작업 규칙")
        self.assertEqual(lines[i - 1], "")
        self.assertIn("P1 반영", lines[i - 2])

    def test_fix_block_names_review_file_only_when_it_exists(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        show_worker(self, "exited")
        self.orch("fix", "T-001", "--note", "워커 실패 보고 반영")  # working(워커 실패) → 리뷰 파일 없음
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("## 수정 라운드\n", spec)
        self.assertIn("docs/tasks/T-001/report.md", spec)
        self.assertIn('git log --grep "^\\[T-001\\]"', spec)
        self.assertIn("워커 실패 보고 반영", spec)
        self.assertNotIn("docs/reviews/T-001/r1.md", spec)
        self.assertNotIn("\n\n\n", spec)
        review = self.root / "docs/reviews/T-001/r2.md"
        review.parent.mkdir(parents=True)
        review.write_text("VERDICT: REQUEST_CHANGES\n", encoding="utf-8")
        self.set_state("T-001", status="changes")
        self.orch("fix", "T-001")
        self.assertIn("docs/reviews/T-001/r2.md", self.arg(self.last_start(), "--spec"))


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class TestBlockTest(TaskCase):
    def setUp(self):
        super().setUp()
        self.quick, self.full = fake_test_commands(self)
        write_test_config(self.root, self.quick, self.full)
        self.commit_plan("chore: test 설정")

    def commit_plan(self, message):
        """[test] 는 커밋된 HEAD 에서 읽으므로 tasks.toml 을 바꾸면 커밋한다(지웠으면 지움을 커밋한다)."""
        self.git("add", "-A", "--", "docs/planning/tasks.toml")
        self.git("commit", "-q", "-m", message)

    def spec_for(self, task_id, role, extra=""):
        self.write_spec(task_id, role=role, extra=extra)
        self.orch("start", task_id)
        return self.arg(self.last_start(), "--spec")

    def test_developer_gets_quick_rules(self):
        spec = self.spec_for("T-001", "developer")
        self.assertIn("묻는다.\n\n## 테스트\n", spec)
        self.assertIn("`.orchestration/bin/orch test T-001` 가 통과해야 한다", spec)
        self.assertIn("전체 테스트는 돌리지 않는다", spec)
        order = ("작업을 `[T-001]` 커밋으로 먼저 커밋한다", "`.orchestration/bin/orch test T-001` 를 돌린다",
                 "리포트를 따로 커밋한다")
        self.assertEqual([spec.index(text) for text in order], sorted(spec.index(text) for text in order))
        self.assertIn("테스트가 소유 경로에 만드는 산출물(`__pycache__`, 스냅숏 등)은 `.gitignore` 에 넣는다. "
                      "무시되지 않은 새 파일이 있으면 orch test 가 거부한다", spec)
        self.assertLess(spec.index("## 테스트"), spec.index("## 작업 규칙"))
        self.assertNotIn("\n\n\n", spec)
        self.assertNotIn("{test_block}", spec)

    def test_tester_gets_full_once(self):
        spec = self.spec_for("T-001", "tester", "owns: tests/grp/**, docs/qa/grp.md\n")
        for text in ("`.orchestration/bin/orch test T-001 --full`", "제품 코드는 고치지 않는다", "약하게",
                     "다시 돌리지 않는다"):
            self.assertIn(text, spec)
        self.assertIn("고친 테스트를 커밋한 뒤", spec)
        self.assertNotIn("worker_done 전에 `.orchestration/bin/orch test T-001` 가 통과해야 한다", spec)

    def test_other_roles_and_no_config_have_no_block(self):
        self.assertNotIn("## 테스트", self.spec_for("T-001", "designer"))
        (self.root / "docs/planning/tasks.toml").unlink()
        self.commit_plan("chore: test 설정 지움")
        self.assertNotIn("\n## 테스트\n", self.spec_for("T-002", "developer", "owns: src/other/**\n"))

    def test_broken_test_table_has_no_block(self):
        write_test_config(self.root, "npx vitest", self.full)
        self.commit_plan("chore: 틀린 test 설정")
        self.assertNotIn("\n## 테스트\n", self.spec_for("T-001", "developer"))

    def test_uncommitted_test_table_has_no_block(self):
        (self.root / "docs/planning/tasks.toml").unlink()
        self.commit_plan("chore: test 설정 지움")
        write_test_config(self.root, self.quick, self.full)
        self.assertNotIn("\n## 테스트\n", self.spec_for("T-001", "developer"))

    def test_committed_table_survives_worktree_edit(self):
        (self.root / "docs/planning/tasks.toml").write_text("[test\n", encoding="utf-8")
        self.assertIn("\n## 테스트\n", self.spec_for("T-001", "developer"))

    def test_fix_stage_keeps_spacing(self):
        self.spec_for("T-001", "developer")
        self.set_state("T-001", status="changes")
        self.orch("fix", "T-001", "--note", "P1 반영")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("코디네이터 지시: P1 반영\n\n## 테스트\n", spec)
        self.assertNotIn("\n\n\n", spec)


class LaunchCrashWindowTest(TaskCase):
    def project(self):
        p = Project(self.root)
        return p, Config.load(p.config_path)

    def launcher_died(self, task_id):
        """테스트 안에서 모사한 크래시라 pending 기록의 pid(이 테스트 프로세스)가 살아 있다. 죽은 orch 의 pid 로 바꾼다."""
        st = self.state(task_id)
        st["history"][-1]["pid"] = dead_pid()
        self.set_state(task_id, history=st["history"])

    def test_launch_records_pid_in_pending_entry(self):
        self.write_spec("T-001")
        p, cfg = self.project()
        seen = {}
        real_call = orca.call

        def spy(cmd, check=True):
            seen["state"] = self.state("T-001")
            return real_call(cmd, check=check)

        with mock.patch.dict(os.environ, self.env), mock.patch.object(orca, "call", spy):
            tasks.start(p, cfg, "T-001")
        pending = seen["state"]["history"][-1]
        self.assertEqual((pending["pending"], pending["pid"]), (True, os.getpid()))
        self.assertTrue(all("pending" not in h for h in self.state("T-001")["history"]))

    def test_state_persisted_as_launch_failed_before_orca_call(self):
        self.write_spec("T-001")
        p, cfg = self.project()
        seen = {}

        def crash(cmd, check=True):
            seen["state"] = self.state("T-001")
            raise RuntimeError("orch 비정상 종료 모사")

        with mock.patch.dict(os.environ, self.env), mock.patch.object(orca, "call", crash):
            with self.assertRaises(RuntimeError):
                tasks.start(p, cfg, "T-001")
        self.launcher_died("T-001")
        self.assertEqual(seen["state"]["status"], "launch_failed")
        self.assertEqual(seen["state"]["stage"], "work")
        self.assertEqual(self.state("T-001")["status"], "launch_failed")
        self.assertEqual(self.orch("start", "T-001", check=False).returncode, 2)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])
        self.orch("start", "T-001", "--retry")
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_unparseable_output_leaves_launch_failed_and_refuses_rerun(self):
        self.respond_raw("orchestration worker-start", "not json at all", code=0)
        self.write_spec("T-001")
        self.assertNotEqual(self.orch("start", "T-001", check=False).returncode, 0)
        self.assertEqual(self.state("T-001")["status"], "launch_failed")
        self.assertEqual(self.orch("start", "T-001", check=False).returncode, 2)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)
        self.respond("orchestration worker-start", STARTED)
        self.orch("start", "T-001", "--retry")
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_unparseable_output_clears_pending_entry_and_shows_recovery(self):
        self.respond_raw("orchestration worker-start", "not json at all", code=0)
        self.write_spec("T-001")
        self.assertNotEqual(self.orch("start", "T-001", check=False).returncode, 0)
        st = self.state("T-001")
        self.assertEqual(st["status"], "launch_failed")
        self.assertTrue(all("pending" not in h for h in st["history"]))
        self.assertFalse(tasks.launch_in_progress(st))
        row = json.loads(self.orch("status").stdout)["tasks"][0]
        self.assertNotIn("launching", row)
        self.assertIn("orch start T-001 --retry", row["next"])

    def test_fix_crash_window_guards_rerun(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        self.set_state("T-001", status="changes")
        p, cfg = self.project()

        def crash(cmd, check=True):
            if cmd[:2] == ["orchestration", "worker-start"]:
                raise RuntimeError("crash")
            return 0, {"ok": True, "result": {}}

        with mock.patch.dict(os.environ, self.env), mock.patch.object(orca, "call", crash):
            with self.assertRaises(RuntimeError):
                tasks.fix(p, cfg, "T-001")
        self.launcher_died("T-001")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["stage"], st["round"]), ("launch_failed", "fix", 2))
        self.assertEqual(self.orch("fix", "T-001", check=False).returncode, 2)
        self.orch("fix", "T-001", "--retry")
        self.assertEqual(self.state("T-001")["status"], "working")


class LaunchInProgressTest(TaskCase):
    """launch 가 Orca 를 부르는 동안 status 는 launch_failed 로 남지만 실패로 안내하지 않는다(F4).
    진행 중: 마지막 history 가 pending 이고, 그 pid 가 살아 있고, 10분이 지나지 않았다."""

    def setUp(self):
        super().setUp()
        self.write_spec("T-001")
        self.orch("start", "T-001")

    def pend(self, pid, age=0, stage="work", rnd=1):
        at = (datetime.now().astimezone() - timedelta(seconds=age)).isoformat(timespec="seconds")
        history = self.state("T-001")["history"]
        history.append({"stage": stage, "round": rnd, "at": at, "ok": None, "pending": True, "pid": pid})
        self.set_state("T-001", status="launch_failed", stage=stage, round=rnd, history=history)

    def row(self):
        return json.loads(self.orch("status").stdout)["tasks"][0]

    def test_live_work_launch_waits_and_refuses_start_retry(self):
        self.pend(os.getpid())
        row = self.row()
        self.assertEqual(row["status"], "launch_failed")
        self.assertIs(row["launching"], True)
        self.assertEqual(row["next"], "워커를 띄우는 중이다. 끝날 때까지 기다린 뒤 orch status 로 다시 본다")
        for args in (("start", "T-001", "--retry"), ("start", "T-001")):
            proc = self.orch(*args, check=False)
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("아직 실행 중", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)

    def test_live_fix_launch_refuses_fix_retry(self):
        self.pend(os.getpid(), stage="fix", rnd=2)
        proc = self.orch("fix", "T-001", "--retry", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("아직 실행 중", proc.stderr)
        self.assertIs(self.row()["launching"], True)
        self.assertEqual(self.calls_for("orchestration worker-release"), [])

    def assert_plain_launch_failed(self):
        row = self.row()
        self.assertNotIn("launching", row)
        self.assertIn("orca orchestration worker-list", row["next"])
        self.assertIn("orch start T-001 --retry", row["next"])
        self.orch("start", "T-001", "--retry")
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_dead_pid_keeps_launch_failed_recovery(self):
        self.pend(dead_pid())
        self.assert_plain_launch_failed()

    def test_pending_entry_older_than_10_minutes_is_not_in_progress(self):
        self.pend(os.getpid(), age=11 * 60)
        self.assert_plain_launch_failed()

    def test_drop_refuses_while_launch_is_running(self):
        self.pend(os.getpid())
        proc = self.orch("drop", "T-001", "--reason", "중단", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("launch", proc.stderr)
        self.assertIn("orch status", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "launch_failed")

    def test_drop_proceeds_when_launch_pid_is_dead(self):
        self.pend(dead_pid())
        self.orch("drop", "T-001", "--reason", "중단")
        self.assertEqual(self.state("T-001")["status"], "dropped")

    def test_helper_rejects_incomplete_records(self):
        at = datetime.now().astimezone().isoformat(timespec="seconds")
        live = {"stage": "work", "round": 1, "at": at, "ok": None, "pending": True, "pid": os.getpid()}
        self.assertTrue(tasks.launch_in_progress({"status": "launch_failed", "history": [live]}))
        for st in ({}, {"status": "launch_failed", "history": []},
                   {"status": "working", "history": [live]},
                   {"status": "launch_failed", "history": [live, {**live, "pending": None, "ok": False}]},
                   {"status": "launch_failed", "history": [{k: v for k, v in live.items() if k != "pid"}]},
                   {"status": "launch_failed", "history": [{**live, "pid": True}]},
                   {"status": "launch_failed", "history": [{**live, "pid": 0}]},
                   {"status": "launch_failed", "history": [{**live, "at": "어제"}]},
                   {"status": "launch_failed", "history": [{**live, "at": "2026-10-31T14:02:45"}]}):
            with self.subTest(st=st):
                self.assertFalse(tasks.launch_in_progress(st))


class FixAcceptTest(TaskCase):
    def setUp(self):
        super().setUp()
        self.write_spec("T-001")
        self.orch("start", "T-001")

    def test_fix_releases_old_dispatch_then_starts_fresh_worker(self):
        self.set_state("T-001", status="changes")
        review = self.root / "docs/reviews/T-001/r1.md"
        review.parent.mkdir(parents=True)
        review.write_text("VERDICT: REQUEST_CHANGES\n", encoding="utf-8")
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w1"}, "projection": {"liveness": {"verdict": "live"}}}})
        self.orch("fix", "T-001", "--note", "P1 반영")
        call = self.last_start()
        self.assertNotIn("--terminal", call)
        self.assertEqual(self.arg(call, "--worktree"), "current")
        self.assertEqual(self.arg(call, "--agent"), "claude")
        self.assertEqual(self.arg(call, "--model"), "opus")
        self.assertEqual(self.arg(call, "--effort"), "xhigh")
        spec = self.arg(call, "--spec")
        self.assertIn("docs/reviews/T-001/r1.md", spec)
        self.assertIn("P1 반영", spec)
        self.assertEqual(self.state("T-001")["round"], 2)
        order = [c[:2] for c in self.calls() if c[:2] in (["orchestration", "worker-release"],
                                                          ["orchestration", "worker-start"])]
        self.assertEqual(order[-2:], [["orchestration", "worker-release"], ["orchestration", "worker-start"]])
        release = self.calls_for("orchestration worker-release")[-1]
        self.assertEqual(self.arg(release, "--dispatch"), "ctx_1")

    def test_fix_honors_model_and_effort_overrides(self):
        self.set_state("T-001", status="changes")
        self.orch("fix", "T-001", "--model", "sonnet", "--effort", "high")
        call = self.last_start()
        self.assertEqual(self.arg(call, "--model"), "sonnet")
        self.assertEqual(self.arg(call, "--effort"), "high")
        self.assertNotIn("--terminal", call)

    def test_fix_records_release_failure_and_still_launches(self):
        self.set_state("T-001", status="changes")
        self.respond_raw("orchestration worker-release", "not json at all")
        self.orch("fix", "T-001")
        st = self.state("T-001")
        self.assertEqual(st["status"], "working")
        self.assertIs(st["history"][-1]["previous_released"], False)
        self.assertIn("previous_release_error", st["history"][-1])
        self.assertNotIn("terminal_reused", st["history"][-1])

    def test_fix_on_working_with_busy_worker_is_refused(self):
        show_worker(self, "live", stage="running")
        proc = self.orch("fix", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--force", proc.stderr)
        self.assertEqual(self.calls_for("orchestration worker-release"), [])
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_fix_on_working_with_unknown_worker_is_refused(self):
        self.respond("orchestration worker-show", {"ok": False, "error": {"code": "x"}}, code=1)
        self.assertEqual(self.orch("fix", "T-001", check=False).returncode, 2)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)

    def test_fix_on_working_with_settled_but_live_worker_is_allowed(self):
        show_worker(self, "live", stage="settled", state="succeeded")
        self.orch("fix", "T-001", "--note", "워커 종료")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 2)
        self.assertFalse(self.state("T-001")["history"][-1]["forced"])

    def test_fix_on_working_with_dead_worker_is_allowed(self):
        show_worker(self, "exited")
        self.orch("fix", "T-001")
        self.assertEqual(self.state("T-001")["round"], 2)

    def test_fix_force_overrides_busy_worker_and_records_it(self):
        show_worker(self, "live", stage="running")
        self.orch("fix", "T-001", "--force")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 2)
        self.assertTrue(self.state("T-001")["history"][-1]["forced"])

    def test_fix_on_changes_does_not_check_worker(self):
        self.set_state("T-001", status="changes")
        show_worker(self, "live", stage="running")
        self.orch("fix", "T-001")
        self.assertEqual(self.state("T-001")["round"], 2)

    def test_fix_stops_at_max_rounds(self):
        self.set_state("T-001", status="changes", round=3)
        proc = self.orch("fix", "T-001", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("최대 3 라운드", proc.stderr)

    def test_fix_requires_changes_or_working(self):
        self.set_state("T-001", status="approved")
        self.assertEqual(self.orch("fix", "T-001", check=False).returncode, 2)

    def test_accept_releases_and_unlocks(self):
        self.set_state("T-001", status="approved")
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertTrue(out["released"])
        call = self.calls_for("orchestration worker-release")[-1]
        self.assertEqual(self.arg(call, "--dispatch"), "ctx_1")
        self.assertEqual(self.state("T-001")["status"], "done")
        self.write_spec("T-002")
        self.orch("start", "T-002")

    def test_fix_rechecks_lock_with_edited_owns(self):
        self.write_spec("T-002", role="tester")
        self.orch("start", "T-002")
        self.set_state("T-001", status="changes")
        self.write_spec("T-001", extra="owns: tests/unit/**\n")
        before = len(self.calls_for("orchestration worker-start"))
        proc = self.orch("fix", "T-001", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("T-002", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), before)
        self.assertEqual(self.state("T-001")["status"], "changes")

    def test_accept_requires_approved(self):
        self.assertEqual(self.orch("accept", "T-001", check=False).returncode, 2)

    def test_which_maps_orca_ids(self):
        self.assertEqual(json.loads(self.orch("which", "ctx_1").stdout)["id"], "T-001")
        self.assertEqual(json.loads(self.orch("which", "task_1").stdout)["id"], "T-001")
        self.assertEqual(self.orch("which", "ctx_zzz", check=False).returncode, 1)


class FixSupplementTest(TaskCase):
    """디자인 시스템 보강(director) 뒤 화면을 다시 고치는 fix 는 --supplement 로 보강 태스크를 이력에 남긴다(사용자 요청 아님)."""

    def setUp(self):
        super().setUp()
        self.write_spec("T-001", role="designer", title="로그인 화면")
        self.orch("start", "T-001")
        self.set_state("T-001", status="awaiting_user", released=True, round=3)
        self.put_state("T-002", role="director", status="done")

    def put_state(self, task_id, role, status):
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.write_text(json.dumps({"id": task_id, "role": role, "title": "디자인 시스템 보강", "status": status,
                                    "round": 1, "owns": ["docs/design/system/**"], "history": []}), encoding="utf-8")

    def test_supplement_is_recorded_instead_of_user_request(self):
        self.orch("fix", "T-001", "--supplement", "T-002", "--note", "디자인 시스템 보강(T-002)을 반영한다")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 4))  # awaiting_user 는 최대 라운드를 넘어도 고친다
        self.assertEqual(st["history"][-1]["supplement"], "T-002")
        self.assertNotIn("user_requested", st["history"][-1])
        self.assertIn("디자인 시스템 보강(T-002)을 반영한다", self.arg(self.last_start(), "--spec"))

    def test_without_supplement_awaiting_user_fix_is_user_request(self):
        self.orch("fix", "T-001", "--note", "버튼을 키워 주세요")
        entry = self.state("T-001")["history"][-1]
        self.assertTrue(entry["user_requested"])
        self.assertNotIn("supplement", entry)

    def test_supplement_must_be_done_director_task_without_retry(self):
        self.put_state("T-003", role="developer", status="done")
        self.put_state("T-004", role="director", status="working")
        cases = {"T-009": "T-009", "T-003": "role=developer", "T-004": "status=working", "X-1": "X-1"}
        for ref, text in cases.items():
            with self.subTest(ref=ref):
                proc = self.orch("fix", "T-001", "--supplement", ref, check=False)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("--supplement", proc.stderr)
                self.assertIn(text, proc.stderr)
        proc = self.orch("fix", "T-001", "--supplement", "T-002", "--retry", check=False)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--retry", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)
        self.assertEqual(self.state("T-001")["status"], "awaiting_user")


class StartRefusalTest(TaskCase):
    def test_launch_failed_work_points_to_start_retry_not_fix(self):
        self.respond("orchestration worker-start", {"ok": False, "error": {"code": "x"}}, code=1)
        self.write_spec("T-001")
        self.orch("start", "T-001", check=False)
        proc = self.orch("start", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("orch start T-001 --retry", proc.stderr)
        self.assertIn("orca orchestration worker-list", proc.stderr)
        self.assertNotIn("orch fix", proc.stderr)

    def test_launch_failed_fix_points_to_fix_retry(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        self.set_state("T-001", status="launch_failed", stage="fix", round=2)
        for args in (("start", "T-001"), ("start", "T-001", "--retry")):
            proc = self.orch(*args, check=False)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("orch fix T-001 --retry", proc.stderr)


class DropTest(TaskCase):
    def setUp(self):
        super().setUp()
        self.write_spec("T-001")
        self.orch("start", "T-001")

    def drop(self, *extra, check=True, env=None):
        return self.orch("drop", "T-001", "--reason", "사용자가 중단을 결정", *extra, check=check, env=env)

    def liveness(self, verdict):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w1"}, "projection": {"liveness": {"verdict": verdict}}}})

    def test_working_task_with_live_worker_is_refused(self):
        self.liveness("live")
        proc = self.drop(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--force", proc.stderr)
        self.assertIn("worker_done", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])
        self.assertEqual(self.arg(self.calls_for("orchestration worker-show")[-1], "--dispatch"), "ctx_1")

    def test_working_task_with_unknown_liveness_is_refused(self):
        self.respond("orchestration worker-show", {"ok": False, "error": {"code": "x"}}, code=1)
        self.assertEqual(self.drop(check=False).returncode, 2)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_working_task_with_settled_but_live_worker_is_dropped_without_force(self):
        show_worker(self, "live", stage="settled", state="succeeded")
        out = json.loads(self.drop().stdout)
        self.assertEqual(out["status"], "dropped")
        self.assertFalse(self.state("T-001")["history"][-1]["forced"])

    def test_working_task_with_dead_worker_is_dropped(self):
        self.liveness("exited")
        self.assertEqual(json.loads(self.drop().stdout)["status"], "dropped")
        self.assertFalse(self.state("T-001")["history"][-1]["forced"])

    def test_force_drops_live_worker_and_records_it(self):
        self.liveness("live")
        out = json.loads(self.drop("--force").stdout)
        self.assertEqual((out["status"], out["forced"]), ("dropped", True))
        entry = self.state("T-001")["history"][-1]
        self.assertEqual((entry["stage"], entry["forced"]), ("drop", True))
        self.assertTrue(self.state("T-001")["history"][-1]["forced"])

    def outbox(self):
        path = self.root / ".orchestration/state/outbox.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_drop_releases_lock_so_overlapping_task_can_start(self):
        self.set_state("T-001", status="changes", round=3)
        out = json.loads(self.drop().stdout)
        self.assertEqual((out["status"], out["released"]), ("dropped", True))
        self.assertEqual(self.arg(self.calls_for("orchestration worker-release")[-1], "--dispatch"), "ctx_1")
        st = self.state("T-001")
        self.assertEqual(st["status"], "dropped")
        self.assertEqual((st["history"][-1]["stage"], st["history"][-1]["reason"]), ("drop", "사용자가 중단을 결정"))
        self.write_spec("T-002")
        self.orch("start", "T-002")
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_drop_from_main_without_drive_is_refused(self):
        proc = self.drop(check=False, env={"ORCA_TERMINAL_HANDLE": "term_main"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("/orca-team:up", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])

    def test_release_error_is_recorded_not_fatal(self):
        self.liveness("exited")
        self.respond_raw("orchestration worker-release", "not json at all")
        out = json.loads(self.drop().stdout)
        self.assertFalse(out["released"])
        self.assertIn("파싱 실패", out["release_error"])
        self.assertEqual(self.state("T-001")["status"], "dropped")

    def test_launch_failed_without_dispatch_skips_release(self):
        self.set_state("T-001", status="launch_failed", dispatch_id=None)
        out = json.loads(self.drop().stdout)
        self.assertEqual(out["status"], "dropped")
        self.assertIsNone(out["released"])
        self.assertEqual(self.calls_for("orchestration worker-release"), [])

    def test_drop_refuses_finished_task_and_empty_reason(self):
        self.assertEqual(self.orch("drop", "T-001", "--reason", "  ", check=False).returncode, 2)
        self.set_state("T-001", status="done")
        self.assertEqual(self.drop(check=False).returncode, 2)

    def test_drop_refused_while_review_running(self):
        self.set_state("T-001", status="reviewing")
        pid = self.root / ".orchestration/state/tasks/T-001/review.pid"
        pid.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.assertEqual(self.drop(check=False).returncode, 3)
        self.assertEqual(self.state("T-001")["status"], "reviewing")

    def test_which_and_status_show_dropped(self):
        self.liveness("exited")
        self.drop()
        self.assertEqual(json.loads(self.orch("which", "ctx_1").stdout)["status"], "dropped")
        task = json.loads(self.orch("status").stdout)["tasks"][0]
        self.assertEqual((task["id"], task["status"], task["next"]), ("T-001", "dropped", None))
        proc = self.orch("start", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("orch task new", proc.stderr)


class NextActionTest(TaskCase):
    """코디네이터 재시작 복구: orch status 가 태스크마다 다음 행동을 알려 준다."""

    def setUp(self):
        super().setUp()
        self.write_spec("T-001")
        self.orch("start", "T-001")

    def next(self):
        return json.loads(self.orch("status").stdout)["tasks"][0]["next"]

    def liveness(self, verdict):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w1"}, "projection": {"liveness": {"verdict": verdict}}}})

    def test_working_with_live_worker_waits(self):
        self.liveness("live")
        self.assertIn("worker_done", self.next())
        self.assertEqual(self.arg(self.calls_for("orchestration worker-show")[-1], "--dispatch"), "ctx_1")

    def test_working_with_busy_worker_waits(self):
        show_worker(self, "live", stage="running")
        self.assertEqual(self.next(), "워커의 worker_done 을 기다린다")

    def test_working_with_settled_succeeded_worker_reviews_in_background(self):
        show_worker(self, "live", stage="settled", state="succeeded")
        nxt = self.next()
        self.assertTrue(nxt.startswith("orch review T-001"), nxt)
        self.assertIn("run_in_background: true", nxt)

    def test_working_with_settled_failed_worker_fixes(self):
        show_worker(self, "live", stage="settled", state="failed")
        self.assertEqual(self.next(), 'orch fix T-001 --note "워커 종료"')

    def test_working_with_dead_worker_fixes(self):
        self.liveness("exited")
        self.assertEqual(self.next(), 'orch fix T-001 --note "워커 종료"')

    def test_working_with_unknown_liveness_waits(self):
        self.respond("orchestration worker-show", {"ok": False, "error": {"code": "x"}}, code=1)
        nxt = self.next()
        self.assertIn("worker_done", nxt)
        self.assertNotIn("orch fix", nxt)

    def test_reviewing_with_live_review_waits(self):
        self.set_state("T-001", status="reviewing")
        (self.root / ".orchestration/state/tasks/T-001/review.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        nxt = self.next()
        self.assertIn("리뷰", nxt)
        self.assertNotIn("orch review", nxt)

    def test_reviewing_without_review_process_reruns_review_in_background(self):
        self.set_state("T-001", status="reviewing")
        nxt = self.next()
        self.assertTrue(nxt.startswith("orch review T-001"), nxt)
        self.assertIn("run_in_background: true", nxt)

    def test_broken_config_falls_back_without_max_rounds(self):
        self.set_state("T-001", status="changes", round=3)
        (self.root / ".orchestration/config.toml").write_text("roles = [깨진\n", encoding="utf-8")
        out = json.loads(self.orch("status").stdout)
        self.assertIn("config.toml", out["config_error"])
        self.assertEqual(out["tasks"][0]["next"], "orch fix T-001")

    def test_status_survives_non_utf8_config(self):
        (self.root / ".orchestration/config.toml").write_bytes(b"\xff\xfe\x00bad")
        out = json.loads(self.orch("status").stdout)
        self.assertIn("config.toml", out["config_error"])

    def test_status_survives_malformed_max_rounds(self):
        self.set_state("T-001", status="changes", round=3)
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_rounds = 3", 'max_rounds = "abc"'), encoding="utf-8")
        proc = self.orch("status", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("max_rounds", out["config_error"])
        self.assertEqual(out["tasks"][0]["next"], "orch fix T-001")

    def test_status_has_null_config_error_when_config_ok(self):
        self.assertIsNone(json.loads(self.orch("status").stdout)["config_error"])

    def test_approved_accepts(self):
        self.set_state("T-001", status="approved")
        self.assertEqual(self.next(), "orch accept T-001")

    def test_changes_fixes(self):
        self.set_state("T-001", status="changes")
        self.assertEqual(self.next(), "orch fix T-001")

    def test_changes_at_max_rounds_asks_main_then_drop(self):
        self.set_state("T-001", status="changes", round=3)
        nxt = self.next()
        self.assertIn("orch notify --type question", nxt)
        self.assertIn("orch drop T-001", nxt)
        self.assertNotIn("orch fix", nxt)

    def test_launch_failed_work_stage(self):
        self.set_state("T-001", status="launch_failed", stage="work")
        nxt = self.next()
        self.assertIn("orca orchestration worker-list", nxt)
        self.assertIn("orch start T-001 --retry", nxt)
        self.assertNotIn("orch fix", nxt)

    def test_launch_failed_fix_stage(self):
        self.set_state("T-001", status="launch_failed", stage="fix", round=2)
        nxt = self.next()
        self.assertIn("orch fix T-001 --retry", nxt)
        self.assertNotIn("orch start", nxt)

    def test_done_and_dropped_have_no_next(self):
        for status in ("done", "dropped"):
            self.set_state("T-001", status=status)
            self.assertIsNone(self.next(), status)


class DependsPendingTest(TaskCase):
    """plan apply 가 만드는 pending 태스크와 front matter depends_on."""

    def write_pending(self, task_id, role="developer", title="로그인", depends_on=(), owns=("src/**",)):
        state = {"id": task_id, "role": role, "title": title, "status": "pending", "stage": "work", "round": 1,
                 "base": None, "owns": list(owns), "depends_on": list(depends_on), "plan_key": task_id.lower(),
                 "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}}
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

    def next_of(self, task_id):
        return {t["id"]: t["next"] for t in json.loads(self.orch("status").stdout)["tasks"]}[task_id]

    def test_deps_of_reads_front_matter_and_validate_rejects_bad_ids(self):
        self.write_spec("T-002", extra="depends_on: T-001, T-003\n")
        doc = tasks.parse_task(tasks.task_path(Project(self.root), "T-002"))
        self.assertEqual(tasks.deps_of(doc), ["T-001", "T-003"])
        self.write_spec("T-004", extra="depends_on: login, T-004\n")
        proc = self.orch("start", "T-004", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("depends_on=login, T-004", proc.stderr)

    def test_start_refuses_until_dependency_done(self):
        self.write_spec("T-001")
        self.write_spec("T-002", role="tester", extra="depends_on: T-001\n")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(없음)", proc.stderr)
        self.orch("start", "T-001")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(working)", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)
        self.set_state("T-001", status="done")
        self.orch("start", "T-002")
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_start_launches_pending_task_and_keeps_plan_fields(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        self.assertEqual(self.orch("start", "T-001", "--retry", check=False).returncode, 2)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])
        self.orch("start", "T-001")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["plan_key"], st["depends_on"]), ("working", 1, "t-001", []))
        self.assertEqual(st["base"], self.git("rev-parse", "HEAD").strip())

    def test_pending_task_does_not_hold_lock(self):
        self.write_spec("T-001")
        self.write_pending("T-001", owns=("src/**",))
        self.write_spec("T-002")
        self.orch("start", "T-002")
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_worker_spec_names_dependencies_and_contracts(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        self.set_state("T-001", status="done")
        self.write_spec("T-002", role="tester", extra="depends_on: T-001\n")
        self.orch("start", "T-002")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("선행 태스크: T-001.", spec)
        self.assertIn("docs/planning/contracts.md", spec)
        self.assertIn("orch ask-user", spec)
        self.write_spec("T-003", role="planner")
        self.orch("start", "T-003")
        self.assertIn("선행 태스크: 없음.", self.arg(self.last_start(), "--spec"))

    def test_drop_pending_task_skips_release(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        out = json.loads(self.orch("drop", "T-001", "--reason", "범위 제외").stdout)
        self.assertEqual((out["status"], out["released"]), ("dropped", None))
        self.assertEqual(self.calls_for("orchestration worker-release"), [])

    def test_pending_next_is_start_ready(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        self.assertEqual(self.next_of("T-001"), "orch start-ready")

    def test_pending_with_dropped_dependency_asks_main_then_drop(self):
        self.write_pending("T-001")
        self.set_state("T-001", status="dropped")
        self.write_pending("T-002", role="tester", depends_on=("T-001",), owns=("tests/**",))
        nxt = self.next_of("T-002")
        self.assertIn("T-001", nxt)
        self.assertIn("orch notify --type question", nxt)
        self.assertIn("orch drop T-002", nxt)

    def test_status_survives_malformed_max_parallel(self):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_parallel = 4", 'max_parallel = "many"'),
                       encoding="utf-8")
        proc = self.orch("status", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("max_parallel", json.loads(proc.stdout)["config_error"])


if __name__ == "__main__":
    unittest.main()
