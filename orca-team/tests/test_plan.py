import json
import os
import subprocess
import unittest
from unittest import mock

from test_tasks import STARTED, TaskCase
from orch import orca, plan
from orch.config import Config
from orch.paths import OrchError, Project

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}
BASIC = [("login-design", "designer", ["docs/design/login/**"], []),
         ("auth-api", "developer", ["src/api/**"], []),
         ("login-ui", "developer", ["src/ui/**"], ["login-design", "auth-api"])]
BAD_FIELDS = """
[[task]]
key = "Bad_Key"
role = "chef"
title = "x"
owns = []
target = "t"
change = "c"
constraints = "k"
acceptance = ["a"]

[[task]]
key = "dup"
role = "developer"
title = "중복 1"
owns = ["src/a/**"]
target = "t"
change = "c"
constraints = "k"
acceptance = ["a"]

[[task]]
key = "dup"
role = "developer"
title = "중복 2"
owns = ["src/b/**"]
target = "t"
change = "c"
constraints = "k"
acceptance = ["a"]

[[task]]
key = "no-accept"
role = "developer"
title = "수용 기준 없음"
owns = ["src/c/**"]
target = "t"
change = "c"
constraints = "k"
"""


def quoted(items) -> str:
    return ", ".join(f'"{x}"' for x in items)


def toml_task(key, role, owns, depends_on=(), title=None) -> str:
    title = title or f"{key} 작업"
    return (f'[[task]]\nkey = "{key}"\nrole = "{role}"\ntitle = "{title}"\n'
            f"owns = [{quoted(owns)}]\ndepends_on = [{quoted(depends_on)}]\n"
            f'target = "{key} 대상"\nchange = "{key} 결과"\nconstraints = "contracts.md 1절을 따른다"\n'
            f'acceptance = ["{key} 확인 명령 통과"]\n')


def write_plan(root, rows) -> None:
    """rows 는 toml_task 인자 튜플 목록이거나 TOML 원문."""
    path = root / plan.PLAN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = rows if isinstance(rows, str) else "\n".join(toml_task(*row) for row in rows)
    path.write_text(text, encoding="utf-8")


DIRECTOR_OWNS = ["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**",
                 "docs/design/pilots/**", "docs/design/system/**"]


class PlanCase(TaskCase):
    """0.3.0 설정(director 직무 없음)으로 도는 그래프 테스트. director 규칙은 DirectorRuleTest 가 템플릿 설정 그대로 본다."""

    def setUp(self):
        super().setUp()
        self.remove_role("director")


class PlanApplyTest(PlanCase):
    def apply(self, *extra, check=True, env=None):
        return self.orch("plan", "apply", *extra, check=check, env=env)

    def spec_files(self):
        return sorted(f.name for f in (self.root / ".orchestration/tasks").glob("T-*.md"))

    def plan_state(self):
        return json.loads((self.root / ".orchestration/state/plan.json").read_text(encoding="utf-8"))

    def assert_nothing_created(self):
        self.assertEqual(self.spec_files(), [])
        self.assertFalse((self.root / ".orchestration/state/plan.json").exists())
        self.assertEqual(list((self.root / ".orchestration/state/tasks").glob("T-*.json")), [])

    def test_apply_writes_specs_commit_plan_and_pending_states(self):
        write_plan(self.root, BASIC)
        out = json.loads(self.apply().stdout)
        self.assertEqual([(t["key"], t["id"]) for t in out["tasks"]],
                         [("login-design", "T-001"), ("auth-api", "T-002"), ("login-ui", "T-003")])
        spec = (self.root / ".orchestration/tasks/T-003.md").read_text(encoding="utf-8")
        for text in ("role: developer", "owns: src/ui/**", "depends_on: T-001, T-002", "plan_key: login-ui",
                     "## Acceptance\n- login-ui 확인 명령 통과"):
            self.assertIn(text, spec)
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[plan] apply: 3 tasks")
        self.assertEqual(sorted(self.git("show", "--name-only", "--format=", "HEAD").split()),
                         [f".orchestration/tasks/T-00{n}.md" for n in (1, 2, 3)])
        self.assertEqual(self.plan_state()["tasks"], {"login-design": "T-001", "auth-api": "T-002", "login-ui": "T-003"})
        st = self.state("T-003")
        self.assertEqual((st["status"], st["depends_on"], st["plan_key"]), ("pending", ["T-001", "T-002"], "login-ui"))
        self.assertEqual(self.calls_for("orchestration worker-start"), [])
        nexts = {t["id"]: t["next"] for t in json.loads(self.orch("status").stdout)["tasks"]}
        self.assertEqual(set(nexts.values()), {"orch start-ready"})

    def test_numbers_continue_after_existing_tasks(self):
        self.orch("task", "new", "planner", "기획")
        write_plan(self.root, BASIC)
        out = json.loads(self.apply().stdout)
        self.assertEqual([t["id"] for t in out["tasks"]], ["T-002", "T-003", "T-004"])
        self.assertIn("depends_on: T-002, T-003",
                      (self.root / ".orchestration/tasks/T-004.md").read_text(encoding="utf-8"))

    def test_rejects_cycle_and_creates_nothing(self):
        write_plan(self.root, [("a", "developer", ["src/a/**"], ["b"]), ("b", "developer", ["src/b/**"], ["a"])])
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("의존 순환: a -> b -> a", proc.stderr)
        self.assert_nothing_created()

    def test_rejects_missing_dependency(self):
        write_plan(self.root, [("a", "developer", ["src/a/**"], ["ghost"])])
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("depends_on 에 없는 key ghost", proc.stderr)
        self.assert_nothing_created()

    def test_rejects_owns_overlap_between_parallel_tasks(self):
        write_plan(self.root, [("a", "developer", ["src/**"], []), ("b", "developer", ["src/ui/**"], [])])
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("a~b", proc.stderr)
        self.assertIn("src/**~src/ui/**", proc.stderr)
        self.assert_nothing_created()

    def test_allows_owns_overlap_between_dependent_tasks(self):
        write_plan(self.root, [("a", "developer", ["src/**"], []), ("b", "developer", ["src/ui/**"], ["a"]),
                               ("c", "developer", ["src/ui/x/**"], ["b"])])
        self.assertEqual(json.loads(self.apply().stdout)["applied"], 3)

    def test_rejects_bad_fields_missing_file_and_broken_toml(self):
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("tasks.toml 이 없다", proc.stderr)
        write_plan(self.root, "[[task]\n")
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("읽기 실패", proc.stderr)
        write_plan(self.root, BAD_FIELDS)
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        for text in ("key 는 영문 소문자", "알 수 없는 role 'chef'", "owns 는", "task[2](dup): key 중복",
                     "task[3](no-accept): 필수 필드 없음 acceptance"):
            self.assertIn(text, proc.stderr)
        self.assert_nothing_created()

    def test_second_apply_needs_append(self):
        write_plan(self.root, BASIC)
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("처음에는 --append 없이", proc.stderr)
        self.apply()
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("orch plan apply --append", proc.stderr)

    def test_append_adds_only_new_keys(self):
        write_plan(self.root, BASIC[:2])
        self.apply()
        write_plan(self.root, BASIC)
        out = json.loads(self.apply("--append").stdout)
        self.assertEqual([(t["key"], t["id"], t["depends_on"]) for t in out["tasks"]],
                         [("login-ui", "T-003", ["T-001", "T-002"])])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[plan] apply: 1 tasks")
        self.assertEqual(list(self.plan_state()["tasks"]), ["login-design", "auth-api", "login-ui"])

    def test_append_rejects_changed_removed_or_no_new_keys(self):
        write_plan(self.root, BASIC[:2])
        self.apply()
        write_plan(self.root, [("login-design", "designer", ["docs/design/login/**"], [], "바뀐 제목"), BASIC[1], BASIC[2]])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("적용한 key 를 바꿨다: login-design", proc.stderr)
        write_plan(self.root, [BASIC[1]])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("적용한 key 가 빠졌다: login-design", proc.stderr)
        write_plan(self.root, BASIC[:2])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("더할 새 key 가 없다", proc.stderr)
        self.assertFalse((self.root / ".orchestration/tasks/T-003.md").exists())

    def test_append_allows_overlap_only_with_finished_tasks(self):
        write_plan(self.root, [("old-ui", "developer", ["src/ui/**"], [])])
        self.apply()
        write_plan(self.root, [("old-ui", "developer", ["src/ui/**"], []), ("new-ui", "developer", ["src/ui/login/**"], [])])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("old-ui~new-ui", proc.stderr)
        self.set_state("T-001", status="done")
        self.assertEqual(json.loads(self.apply("--append").stdout)["tasks"][0]["id"], "T-002")

    def test_commit_failure_removes_written_specs(self):
        write_plan(self.root, BASIC)
        p = Project(self.root)

        def add_then_fail(root, paths, message):
            subprocess.run(["git", "-C", str(root), "add", "--", *paths], check=True, env=self.env)
            raise OrchError("커밋 실패 모사")

        with mock.patch.dict(os.environ, self.env), mock.patch.object(plan, "git_commit_paths", add_then_fail):
            with self.assertRaises(OrchError):
                plan.apply(p, Config.load(p.config_path))
        self.assert_nothing_created()
        self.assertEqual(self.git("ls-files", "--", ".orchestration/tasks"), "")
        self.apply()
        self.assertEqual(self.spec_files(), ["T-001.md", "T-002.md", "T-003.md"])

    def test_coordinator_only(self):
        write_plan(self.root, BASIC)
        self.assertEqual(self.apply(check=False, env=MAIN).returncode, 2)
        self.assert_nothing_created()

    def test_lost_plan_json_refuses_instead_of_duplicating(self):
        write_plan(self.root, BASIC)
        self.apply()
        plan_json = self.root / ".orchestration/state/plan.json"
        for broken in (None, "{깨짐", "[]"):
            with self.subTest(broken=broken):
                if broken is None:
                    plan_json.unlink(missing_ok=True)
                else:
                    plan_json.write_text(broken, encoding="utf-8")
                for extra in ((), ("--append",)):
                    proc = self.apply(*extra, check=False)
                    self.assertEqual(proc.returncode, 2, extra)
                    self.assertIn("plan.json", proc.stderr)
                    self.assertIn("T-001", proc.stderr)
                    self.assertIn("메인에 decide 로 올려", proc.stderr)
                self.assertEqual(self.spec_files(), ["T-001.md", "T-002.md", "T-003.md"])
                self.assertEqual(len(list((self.root / ".orchestration/state/tasks").glob("T-*.json"))), 3)


class PlanCheckTest(PlanCase):
    def check_cmd(self, *extra, check=False, env=None):
        return self.orch("plan", "check", *extra, check=check, env=env)

    def snapshot(self):
        return sorted(str(f.relative_to(self.root)) for f in self.root.rglob("*")
                      if ".git" not in f.parts and f.is_file())

    def test_valid_plan_prints_summary_and_exit_0(self):
        write_plan(self.root, [("a", "designer", ["docs/design/**"], []), ("b", "developer", ["src/b/**"], []),
                               ("c", "developer", ["src/c/**"], ["a", "b"])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["tasks"], 3)
        self.assertEqual(out["roles"], {"designer": 1, "developer": 2})
        self.assertEqual(out["groups"], [["a", "b"], ["c"]])

    def test_invalid_plan_exits_2_with_problems(self):
        write_plan(self.root, [("a", "developer", ["src/**"], ["b"]), ("b", "developer", ["src/**"], ["a"])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("의존 순환", proc.stderr)

    def test_file_option_and_missing_file(self):
        write_plan(self.root, BASIC)
        (self.root / "alt.toml").write_text((self.root / plan.PLAN_FILE).read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / plan.PLAN_FILE).unlink()
        self.assertEqual(self.check_cmd("--file", "alt.toml").returncode, 0)
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("tasks.toml", proc.stderr)

    def test_errors_name_the_checked_file(self):
        alt = self.root / "alt.toml"
        for text, expected in (("[[task]]\nkey = \"a\"\n", "alt.toml 검증 실패"), ("x = [깨짐", "alt.toml 읽기 실패"),
                               ("y = 1\n", "alt.toml: [[task]] 가 없다")):
            with self.subTest(expected=expected):
                alt.write_text(text, encoding="utf-8")
                proc = self.check_cmd("--file", "alt.toml")
                self.assertEqual(proc.returncode, 2)
                self.assertIn(expected, proc.stderr)
                self.assertNotIn(plan.PLAN_FILE, proc.stderr)
        write_plan(self.root, [("a", "developer", ["src/**"], ["b"]), ("b", "developer", ["src/**"], ["a"])])
        alt.write_text((self.root / plan.PLAN_FILE).read_text(encoding="utf-8"), encoding="utf-8")
        proc = self.check_cmd("--file", "alt.toml")
        self.assertIn("alt.toml 검증 실패", proc.stderr)
        self.assertIn("의존 순환", proc.stderr)
        proc = self.check_cmd("--file", "nope.toml")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("nope.toml 이 없다", proc.stderr)

    def test_writes_nothing_and_planner_may_run_it(self):
        write_plan(self.root, BASIC)
        before = self.snapshot()
        self.assertEqual(self.check_cmd(env=MAIN).returncode, 0)  # 코디네이터 전용이 아니다
        self.assertEqual(self.check_cmd().returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.spec_files(), [])

    def spec_files(self):
        return sorted(f.name for f in (self.root / ".orchestration/tasks").glob("T-*.md"))


class PlanReviewTest(PlanCase):
    """기획 태스크가 커밋한 tasks.toml 은 리뷰 사전 검사에서 plan apply 와 같은 규칙으로 검증한다."""

    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.write_spec("T-001", role="planner", title="기획")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec: 기획")
        self.orch("start", "T-001")

    def commit_plan(self, rows):
        write_plan(self.root, rows)
        self.git("add", "--", plan.PLAN_FILE)
        self.git("commit", "-q", "-m", "[T-001] docs: 태스크 그래프")

    def test_invalid_tasks_toml_is_auto_request_changes(self):
        self.commit_plan([("a", "developer", ["src/a/**"], ["b"]), ("b", "developer", ["src/b/**"], ["a"])])
        out = json.loads(self.orch("review", "T-001").stdout)
        self.assertEqual((out["verdict"], out["source"]), ("REQUEST_CHANGES", "orch"))
        review = (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8")
        self.assertIn("tasks.toml", review)
        self.assertIn("의존 순환", review)
        self.assertFalse((self.fake_dir / "codex-run.log").exists())

    def test_valid_tasks_toml_goes_to_codex(self):
        self.commit_plan(BASIC)
        out = json.loads(self.orch("review", "T-001").stdout)
        self.assertEqual((out["verdict"], out["source"]), ("APPROVE", "codex"))


TEST_TABLE = '\n[test]\nquick = "npx vitest related --run --passWithNoTests {files}"\nfull = "npx vitest run"\n'
GROUPED = [("store", "developer", ["src/store/**"], []),
           ("ui", "developer", ["src/ui/**"], ["store"]),
           ("guide", "developer", ["docs/user/**"], []),
           ("grp", "tester", ["tests/grp/**", "docs/qa/grp.md"], ["store", "ui"])]


class GroupRuleTest(PlanCase):
    """[test] 가 있으면 developer 태스크는 tester 태스크(묶음) 하나에 들어야 한다."""

    def write(self, rows, test=True, table=TEST_TABLE):
        write_plan(self.root, "\n".join(toml_task(*row) for row in rows) + (table if test else ""))

    def check(self):
        return self.orch("plan", "check", check=False)

    def test_valid_groups_pass_and_report_test(self):
        self.write(GROUPED)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(json.loads(proc.stdout)["test"], True)

    def test_without_test_table_no_group_rules(self):
        self.write([*GROUPED, ("extra", "developer", ["src/extra/**"], [])], test=False)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(json.loads(proc.stdout)["test"], False)

    def test_ungrouped_developer_rejected(self):
        self.write([*GROUPED, ("extra", "developer", ["src/extra/**"], [])])
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("extra: 문서 전용이 아닌 developer 태스크는 정확히 한 tester 태스크", proc.stderr)
        self.assertIn("지금: 없음", proc.stderr)

    def test_developer_in_two_groups_rejected(self):
        self.write([*GROUPED, ("grp2", "tester", ["tests/grp2/**", "docs/qa/grp2.md"], ["store"])])
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("store: 문서 전용이 아닌", proc.stderr)
        self.assertIn("지금: grp, grp2", proc.stderr)

    def test_tester_owns_must_match_key(self):
        rows = [*GROUPED[:3], ("grp", "tester", ["tests/**"], ["store", "ui"])]
        self.write(rows)
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('grp: tester 태스크 owns 는 정확히 ["tests/grp/**", "docs/qa/grp.md"]', proc.stderr)

    def test_tester_depends_only_on_developers(self):
        rows = [("login-design", "designer", ["docs/design/login/**"], []), *GROUPED[:3],
                ("grp", "tester", ["tests/grp/**", "docs/qa/grp.md"], ["store", "ui", "login-design"])]
        self.write(rows)
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("grp: tester 태스크의 depends_on 에는 developer 태스크만 둔다(login-design)", proc.stderr)

    def test_developer_must_not_depend_on_tester(self):
        rows = [*GROUPED, ("next", "developer", ["src/next/**"], ["grp"]),
                ("grp2", "tester", ["tests/grp2/**", "docs/qa/grp2.md"], ["next"])]
        self.write(rows)
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("next: developer 태스크는 tester 태스크에 의존하지 않는다(grp)", proc.stderr)

    def test_test_table_format_errors_reported(self):
        self.write(GROUPED, table='\n[test]\nquick = "npx vitest"\nfull = "npx vitest run"\n')
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test]: quick 은 {files} 를 정확히 한 번 담는다", proc.stderr)

    def test_append_checks_only_new_tasks(self):
        self.write([("old", "developer", ["src/old/**"], [])], test=False)
        self.orch("plan", "apply")
        self.write([("old", "developer", ["src/old/**"], []), ("new", "developer", ["src/new/**"], []),
                    ("grp", "tester", ["tests/grp/**", "docs/qa/grp.md"], ["new"])])
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.write([("old", "developer", ["src/old/**"], []), ("new2", "developer", ["src/new2/**"], [])])
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("new2:", proc.stderr)
        self.assertNotIn("old:", proc.stderr)


class StartReadyTest(PlanCase):
    """T-001 login-design, T-002 auth-api 는 독립, T-003 login-ui 는 둘에, T-004 login-test 는 T-003 에 의존한다."""

    def setUp(self):
        super().setUp()
        write_plan(self.root, [*BASIC, ("login-test", "tester", ["tests/login/**"], ["login-ui"])])
        self.orch("plan", "apply")

    def ready(self, check=True, env=None):
        return self.orch("start-ready", check=check, env=env)

    def set_max_parallel(self, n):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_parallel = 4", f"max_parallel = {n}"),
                       encoding="utf-8")

    def test_starts_ready_tasks_in_plan_order(self):
        out = json.loads(self.ready().stdout)
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]], [("T-001", "working"), ("T-002", "working")])
        self.assertEqual([(w["id"], w["reason"]) for w in out["waiting"]], [("T-003", "deps"), ("T-004", "deps")])
        self.assertIn("T-001(working)", out["waiting"][0]["detail"])
        self.assertEqual((out["running"], out["max_parallel"]), (2, 4))
        titles = [self.arg(c, "--task-title") for c in self.calls_for("orchestration worker-start")]
        self.assertEqual([t.split()[0] for t in titles], ["T-001", "T-002"])

    def test_limit_caps_working_tasks(self):
        self.set_max_parallel(1)
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001"])
        self.assertEqual(out["waiting"][0], {"id": "T-002", "reason": "limit"})
        self.assertEqual((out["running"], out["max_parallel"]), (1, 1))
        self.assertEqual(json.loads(self.ready().stdout)["started"], [])

    def test_lock_waits_for_overlapping_active_task(self):
        self.write_spec("T-009", title="긴급", extra="owns: src/api/auth/**\n")
        self.orch("start", "T-009")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001"])
        lock = next(w for w in out["waiting"] if w["id"] == "T-002")
        self.assertEqual(lock["reason"], "lock")
        self.assertIn("T-009", lock["detail"])

    def test_dependents_start_after_dependencies_done(self):
        self.ready()
        self.set_state("T-001", status="done")
        self.set_state("T-002", status="done")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-003"])
        self.assertEqual(out["waiting"], [{"id": "T-004", "reason": "deps", "detail": "T-003(working)"}])

    def test_reviewing_task_does_not_count_toward_limit(self):
        self.set_max_parallel(1)
        self.ready()
        self.set_state("T-001", status="reviewing")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-002"])
        self.assertEqual(out["running"], 1)

    def test_broken_spec_waits_with_reason_spec(self):
        self.write_spec("T-002", role="chef")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001"])
        broken = next(w for w in out["waiting"] if w["id"] == "T-002")
        self.assertEqual(broken["reason"], "spec")
        self.assertIn("role=chef", broken["detail"])

    def test_launch_failure_is_reported_and_others_still_start(self):
        p = Project(self.root)

        def call(cmd, check=True):
            if cmd[:2] == ["orchestration", "worker-start"] and cmd[cmd.index("--task-title") + 1].startswith("T-001"):
                return 1, {"ok": False, "error": {"code": "x"}}
            return 0, STARTED

        with mock.patch.dict(os.environ, self.env), mock.patch.object(orca, "call", call):
            out = plan.start_ready(p, Config.load(p.config_path))
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]],
                         [("T-001", "launch_failed"), ("T-002", "working")])
        self.assertIn("--retry", out["started"][0]["error"])
        self.assertEqual(self.state("T-001")["status"], "launch_failed")
        self.assertEqual(out["running"], 1)

    def test_cli_exits_1_when_a_launch_fails(self):
        self.respond("orchestration worker-start", {"ok": False, "error": {"code": "x"}}, code=1)
        proc = self.ready(check=False)
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]],
                         [("T-001", "launch_failed"), ("T-002", "launch_failed")])

    def test_main_without_drive_is_refused(self):
        proc = self.ready(check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("/orca-team:up", proc.stderr)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])


def outbox(root, kind=None):
    path = root / ".orchestration/state/outbox.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [e for e in entries if kind is None or e["type"] == kind]


class ApprovalTest(PlanCase):
    """designer 는 기본 승인 대상 직무다. Codex APPROVE 뒤 accept 하면 awaiting_user 가 된다."""

    def setUp(self):
        super().setUp()
        self.write_spec("T-001", role="designer", title="로그인 화면")
        self.orch("start", "T-001")
        self.set_state("T-001", status="approved")

    def next_of(self, task_id):
        return {t["id"]: t["next"] for t in json.loads(self.orch("status").stdout)["tasks"]}[task_id]

    def test_accept_designer_waits_for_user_and_keeps_lock(self):
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertEqual((out["status"], out["released"]), ("awaiting_user", True))
        self.assertEqual(self.state("T-001")["status"], "awaiting_user")
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)
        self.assertEqual(self.state("T-001")["status"], "awaiting_user")
        self.write_spec("T-002", role="designer", title="회원가입")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("T-001(awaiting_user)", proc.stderr)

    def test_accept_from_awaiting_user_is_user_approval(self):
        self.orch("accept", "T-001")
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertEqual((out["status"], out["user_approved"]), ("done", True))
        st = self.state("T-001")
        self.assertEqual(st["status"], "done")
        self.assertIn("user_approved_at", st)
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)
        self.assertEqual(self.state("T-001")["status"], "done")
        self.write_spec("T-002", role="designer", title="회원가입")
        self.orch("start", "T-002")

    def test_empty_approval_roles_accepts_straight_to_done(self):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('user_approval_roles = ["designer"]',
                                                               "user_approval_roles = []"), encoding="utf-8")
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "done")

    def test_broken_config_falls_back_to_no_approval_gate(self):
        """설정이 깨지면 키가 없을 때와 같은 기본값([])으로 본다."""
        (self.root / ".orchestration/config.toml").write_text("roles = [깨진\n", encoding="utf-8")
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "done")

    def test_invalid_approval_roles_refuses_accept(self):
        """설정은 읽히는데 user_approval_roles 가 틀리면 게이트를 건너뛰지 않고 accept 를 거부한다(워커 해제도 하지 않는다)."""
        cfg = self.root / ".orchestration/config.toml"
        original = cfg.read_text(encoding="utf-8")
        for bad in ('["desginer"]', '"designer"'):
            with self.subTest(bad=bad):
                cfg.write_text(original.replace('user_approval_roles = ["designer"]', f"user_approval_roles = {bad}"),
                               encoding="utf-8")
                proc = self.orch("accept", "T-001", check=False)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("user_approval_roles", proc.stderr)
                self.assertIn("config.toml 을 고친", proc.stderr)
                self.assertEqual(self.state("T-001")["status"], "approved")
                self.assertEqual(self.calls_for("orchestration worker-release"), [])
        cfg.write_text(original, encoding="utf-8")
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "awaiting_user")

    def test_config_without_key_keeps_0_2_0_flow(self):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('user_approval_roles = ["designer"]\n', ""),
                       encoding="utf-8")
        self.assertNotIn("user_approval_roles =", cfg.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "done")

    def test_unknown_approval_role_is_config_error(self):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('user_approval_roles = ["designer"]',
                                                               'user_approval_roles = ["desginer"]'), encoding="utf-8")
        status = json.loads(self.orch("status").stdout)
        self.assertIn("desginer", status["config_error"])
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("desginer", proc.stderr)

    def test_drop_awaiting_user_does_not_release_twice(self):
        self.orch("accept", "T-001")
        out = json.loads(self.orch("drop", "T-001", "--reason", "디자인 취소").stdout)
        self.assertEqual(out["status"], "dropped")
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)  # accept 때 한 번뿐

    def test_drop_awaiting_user_retries_failed_release(self):
        self.respond("orchestration worker-release", {"ok": False, "error": {"code": "x"}}, code=1)
        self.assertFalse(json.loads(self.orch("accept", "T-001").stdout)["released"])
        self.respond("orchestration worker-release", {"ok": True})
        out = json.loads(self.orch("drop", "T-001", "--reason", "디자인 취소").stdout)
        self.assertTrue(out["released"])
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 2)

    def test_fix_from_awaiting_user_starts_new_round_past_max(self):
        self.orch("accept", "T-001")
        self.set_state("T-001", round=3)
        self.orch("fix", "T-001", "--note", "버튼을 키워 주세요")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["stage"]), ("working", 4, "fix"))
        self.assertTrue(st["history"][-1]["user_requested"])
        self.assertIn("버튼을 키워 주세요", self.arg(self.last_start(), "--spec"))
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)  # accept 때 한 번뿐

    def test_next_action_for_awaiting_user(self):
        self.orch("accept", "T-001")  # accept 는 outbox 에 쓰지 않는다
        self.assertIn("orch design-review", self.next_of("T-001"))
        self.orch("design-review")  # out-0001 approval
        nxt = self.next_of("T-001")
        for text in ("out-0001", "orch accept T-001", "orch fix T-001 --note"):
            self.assertIn(text, nxt)


class DesignReviewTest(PlanCase):
    def awaiting(self, task_id, title, owns):
        self.write_spec(task_id, role="designer", title=title, extra=f"owns: {owns}\n")
        self.orch("start", task_id)
        self.set_state(task_id, status="approved")
        self.orch("accept", task_id)

    def test_refuses_while_design_tasks_unfinished(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.write_spec("T-002", role="designer", title="회원가입 화면", extra="owns: docs/design/signup/**\n")
        self.orch("start", "T-002")
        self.write_spec("T-003", title="API")  # developer 는 승인 대상이 아니다
        self.orch("start", "T-003")
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-002(working)", proc.stderr)
        self.assertNotIn("T-003", proc.stderr)
        self.assertEqual(outbox(self.root, "approval"), [])

    def test_refuses_without_awaiting_user(self):
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("awaiting_user", proc.stderr)
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.orch("accept", "T-001")  # 사용자 승인으로 done
        self.assertEqual(self.orch("design-review", check=False).returncode, 2)

    def test_posts_one_approval_with_design_links(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.awaiting("T-002", "회원가입 화면", "docs/design/signup/**")
        report = self.root / "docs/tasks/T-001/report.md"
        report.parent.mkdir(parents=True)
        report.write_text("## r1\n- https://claude.ai/artifact/OLD\n\n## r2\n- 최신(https://claude.ai/code/artifact/1a2b-3c).\n",
                          encoding="utf-8")
        out = json.loads(self.orch("design-review").stdout)
        self.assertEqual(out["tasks"], ["T-001", "T-002"])
        self.assertEqual(out["links"], {"T-001": "https://claude.ai/code/artifact/1a2b-3c", "T-002": None})
        entry = outbox(self.root, "approval")[-1]
        self.assertEqual((entry["id"], entry["tasks"], entry["body"]), (out["id"], ["T-001", "T-002"], "디자인 2개를 확인해 주세요"))
        render = entry["render"]
        self.assertEqual(render["header"], "디자인")
        self.assertEqual(render["question"], "디자인 2개를 확인해 주세요\n"
                                             "예: 위 링크를 화면마다 열어 본다. 고칠 화면은 Other 에 화면과 내용을 적는다")
        self.assertEqual(render["context"], "- T-001 로그인 화면: https://claude.ai/code/artifact/1a2b-3c\n"
                                            "- T-002 회원가입 화면: (링크 없음: docs/tasks/T-002/report.md)")
        self.assertEqual([o["label"] for o in render["options"]], ["전부 승인 (추천)", "수정 요청(화면과 내용을 적어 주세요)"])
        self.assertTrue(render["options"][0]["description"].startswith("추천 사유: "))
        saved = json.loads((self.root / ".orchestration/state/design-review.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["asks"][-1]["tasks"], {"T-001": 1, "T-002": 1})
        proc = self.orch("answer", out["id"], "전부 승인 (추천)", check=False, env=MAIN)  # approval 은 design-decide 로 답한다
        self.assertEqual(proc.returncode, 2)
        self.assertIn("design-decide", proc.stderr)

    def test_same_designs_twice_is_exit_3(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        first = json.loads(self.orch("design-review").stdout)["id"]
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 3)
        self.assertIn(first, proc.stderr)
        self.assertEqual(len(outbox(self.root, "approval")), 1)

    def test_after_fix_asks_only_revised_designs(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.awaiting("T-002", "회원가입 화면", "docs/design/signup/**")
        first = json.loads(self.orch("design-review").stdout)["id"]
        self.orch("accept", "T-001")  # 사용자 승인
        self.orch("fix", "T-002", "--note", "여백을 줄여 주세요")
        self.set_state("T-002", status="approved")
        self.orch("accept", "T-002")  # r2 로 다시 awaiting_user
        out = json.loads(self.orch("design-review").stdout)
        self.assertNotEqual(out["id"], first)
        self.assertEqual(out["tasks"], ["T-002"])
        self.assertTrue(outbox(self.root, "approval")[-1]["render"]["question"].startswith("디자인 1개를 확인해 주세요\n"))

    def test_coordinator_only(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.assertEqual(self.orch("design-review", check=False, env=MAIN).returncode, 2)
        self.assertEqual(outbox(self.root, "approval"), [])

    def test_pending_design_waiting_on_non_design_task_does_not_block(self):
        """--append 로 더한 디자인 태스크가 아직 끝나지 않은 개발 태스크를 기다리면 이번 일괄 승인을 막지 않는다."""
        write_plan(self.root, [("login-design", "designer", ["docs/design/login/**"], []),
                               ("auth-api", "developer", ["src/api/**"], [])])
        self.orch("plan", "apply")
        self.orch("start-ready")
        self.set_state("T-001", status="approved")
        self.orch("accept", "T-001")
        write_plan(self.root, [("login-design", "designer", ["docs/design/login/**"], []),
                               ("auth-api", "developer", ["src/api/**"], []),
                               ("api-icons", "designer", ["docs/design/icons/**"], ["auth-api"])])
        self.orch("plan", "apply", "--append")
        self.assertEqual(self.state("T-003")["status"], "pending")
        out = json.loads(self.orch("design-review").stdout)
        self.assertEqual(out["tasks"], ["T-001"])

    def test_design_link_takes_last_claude_artifact_url(self):
        p = Project(self.root)
        self.assertIsNone(plan.design_link(p, "T-001"))
        report = self.root / "docs/tasks/T-001/report.md"
        report.parent.mkdir(parents=True)
        report.write_text("r1 https://claude.ai/artifact/AAA\nr2 (https://claude.ai/code/artifact/0b1c-2d3e).\n"
                          "참고 https://example.com/artifact/zzz\n", encoding="utf-8")
        self.assertEqual(plan.design_link(p, "T-001"), "https://claude.ai/code/artifact/0b1c-2d3e")

    def test_design_link_skips_project_design_system_url(self):
        p = Project(self.root)
        direction = self.root / "docs/design/direction.md"
        direction.parent.mkdir(parents=True)
        direction.write_text("# 방향\n\ndesign_system_url: https://claude.ai/artifact/SYS\n", encoding="utf-8")
        report = self.root / "docs/tasks/T-001/report.md"
        report.parent.mkdir(parents=True)
        report.write_text("## r1\nDesign: https://claude.ai/artifact/S1\nSystem: https://claude.ai/artifact/SYS\n"
                          "## r2\nDesign: https://claude.ai/artifact/S2\nSystem: https://claude.ai/artifact/SYS\n",
                          encoding="utf-8")
        self.assertEqual(plan.design_link(p, "T-001"), "https://claude.ai/artifact/S2")
        report.write_text("System: https://claude.ai/artifact/SYS\n", encoding="utf-8")
        self.assertIsNone(plan.design_link(p, "T-001"))


class GraphEndToEndTest(PlanCase):
    """가짜 orca·Codex 로 그래프 한 바퀴: plan apply → start-ready → review → accept(designer) → design-review →
    메인 answer → accept(사용자 승인) → 후속 태스크 시작."""

    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        write_plan(self.root, BASIC)
        self.git("add", "--", plan.PLAN_FILE)
        self.git("commit", "-q", "-m", "[T-000] docs: 태스크 그래프")

    def commit_work(self, task_id, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"[{task_id}] feat: {path}")

    def test_design_gate_round_trip(self):
        applied = json.loads(self.orch("plan", "apply").stdout)
        self.assertEqual([t["id"] for t in applied["tasks"]], ["T-001", "T-002", "T-003"])
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001", "T-002"])
        self.assertEqual([(w["id"], w["reason"]) for w in out["waiting"]], [("T-003", "deps")])

        self.commit_work("T-001", "docs/design/login/screen.md", "# 로그인 화면\n")
        self.commit_work("T-001", "docs/tasks/T-001/report.md", "## r1\n- https://claude.ai/artifact/login-1\n")
        self.commit_work("T-002", "src/api/auth.ts", "// auth\n")
        for task_id in ("T-001", "T-002"):
            self.assertEqual(json.loads(self.orch("review", task_id).stdout)["verdict"], "APPROVE", task_id)
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "awaiting_user")
        self.assertEqual(json.loads(self.orch("accept", "T-002").stdout)["status"], "done")
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["started"], [])
        self.assertEqual(out["waiting"], [{"id": "T-003", "reason": "deps", "detail": "T-001(awaiting_user)"}])

        review = json.loads(self.orch("design-review").stdout)
        self.assertEqual((review["tasks"], review["links"]), (["T-001"], {"T-001": "https://claude.ai/artifact/login-1"}))
        status = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual(status["open_questions"], [{"id": review["id"], "type": "approval"}])
        decided = json.loads(self.orch("design-decide", review["id"], "--approve", "T-001").stdout)
        self.assertEqual((decided["approved"], decided["fixed"]), (["T-001"], []))
        self.assertEqual(json.loads(self.orch("status", env=MAIN).stdout)["open_questions"], [])
        self.assertEqual([(s["id"], s["status"]) for s in decided["start_ready"]["started"]], [("T-003", "working")])
        self.assertEqual({t["id"]: t["status"] for t in json.loads(self.orch("status").stdout)["tasks"]},
                         {"T-001": "done", "T-002": "done", "T-003": "working"})


class DesignChainTest(PlanCase):
    """승인 대상 직무끼리 이어진 그래프(디자인 시스템 → 화면). 선행 디자인이 awaiting_user 면 후속 디자인은 시작해
    한 번의 design-review 로 함께 승인받는다. 승인 대상이 아닌 후속(개발)은 done 을 기다린다."""

    def approve(self, task_id):
        self.set_state(task_id, status="approved")
        return json.loads(self.orch("accept", task_id).stdout)["status"]

    def waiting(self, out):
        return {w["id"]: (w["reason"], w.get("detail")) for w in out["waiting"]}

    def test_chained_designs_reach_one_design_review(self):
        write_plan(self.root, [("ds", "designer", ["docs/design/system/**"], []),
                               ("screens", "designer", ["docs/design/screens/**"], ["ds"]),
                               ("ui", "developer", ["src/ui/**"], ["screens"])])
        self.orch("plan", "apply")
        self.assertEqual([s["id"] for s in json.loads(self.orch("start-ready").stdout)["started"]], ["T-001"])
        self.assertEqual(self.approve("T-001"), "awaiting_user")
        proc = self.orch("design-review", check=False)  # T-002 는 곧 시작하므로 기다린다
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-002(pending)", proc.stderr)
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]], [("T-002", "working")])
        self.assertEqual(self.waiting(out), {"T-003": ("deps", "T-002(working)")})
        self.assertEqual(self.approve("T-002"), "awaiting_user")
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["started"], [])
        self.assertEqual(self.waiting(out), {"T-003": ("deps", "T-002(awaiting_user)")})
        review = json.loads(self.orch("design-review").stdout)
        self.assertEqual(review["tasks"], ["T-001", "T-002"])
        self.assertEqual(len(outbox(self.root, "approval")), 1)

    def test_non_design_dependent_waits_for_done_with_reason_deps(self):
        write_plan(self.root, BASIC)
        self.orch("plan", "apply")
        self.orch("start-ready")
        self.assertEqual(self.approve("T-001"), "awaiting_user")
        self.set_state("T-002", status="done")
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["started"], [])
        self.assertEqual(self.waiting(out), {"T-003": ("deps", "T-001(awaiting_user)")})
        proc = self.orch("start", "T-003", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(awaiting_user)", proc.stderr)

    def test_single_start_of_chained_design(self):
        write_plan(self.root, [("ds", "designer", ["docs/design/system/**"], []),
                               ("screens", "designer", ["docs/design/screens/**"], ["ds"])])
        self.orch("plan", "apply")
        self.orch("start", "T-001")
        self.approve("T-001")
        self.orch("start", "T-002")
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_chained_design_locked_by_awaiting_upstream_does_not_deadlock(self):
        """후속 디자인의 소유 경로가 awaiting_user 선행 디자인과 겹치면 사용자 승인 전에는 시작할 수 없다.
        그 태스크를 기다리면 교착이므로 design-review 는 선행 디자인만 묻는다."""
        write_plan(self.root, [("ds", "designer", ["docs/design/**"], []),
                               ("screens", "designer", ["docs/design/screens/**"], ["ds"])])
        self.orch("plan", "apply")
        self.orch("start-ready")
        self.approve("T-001")
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(self.waiting(out)["T-002"][0], "lock")
        self.assertEqual(json.loads(self.orch("design-review").stdout)["tasks"], ["T-001"])

    def test_review_wait_statuses_follow_active(self):
        from orch.tasks import ACTIVE
        self.assertEqual(set(plan.REVIEW_WAIT_STATUSES), set(ACTIVE) - {"awaiting_user"})


class DirectorRuleTest(TaskCase):
    """config 에 director 직무가 있으면(새 프로젝트 템플릿) designer 태스크는 director 태스크 하나에 직간접으로 의존해야 한다."""

    def check_cmd(self):
        return self.orch("plan", "check", check=False)

    def test_designer_must_depend_on_director(self):
        write_plan(self.root, [("design-direction", "director", DIRECTOR_OWNS, []),
                               ("login-design", "designer", ["docs/design/screens/login/**"], [])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn('login-design: designer 태스크는 director 태스크(design-direction)에 직접 또는 간접으로 의존해야 한다. '
                      'depends_on 에 "design-direction" 를 넣는다', proc.stderr)

    def test_director_must_be_exactly_one(self):
        write_plan(self.root, [("login-design", "designer", ["docs/design/screens/login/**"], [])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("designer 태스크가 있으면 director 태스크가 정확히 1개여야 한다(지금: 없음)", proc.stderr)
        write_plan(self.root, [("dir-a", "director", ["docs/design/system/**"], []),
                               ("dir-b", "director", ["docs/design/direction.md"], []),
                               ("login-design", "designer", ["docs/design/screens/login/**"], ["dir-a", "dir-b"])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("(지금: dir-a, dir-b)", proc.stderr)

    def test_indirect_dependency_passes(self):
        write_plan(self.root, [("design-direction", "director", DIRECTOR_OWNS, []),
                               ("common-design", "designer", ["docs/design/screens/common/**"], ["design-direction"]),
                               ("login-design", "designer", ["docs/design/screens/login/**"], ["common-design"])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_asset_only_designer_needs_no_director(self):
        """owns 가 모두 assets/ 아래인 designer 태스크(화면 없이 래스터 에셋만)는 director 규칙에서 뺀다."""
        write_plan(self.root, [("app-icons", "designer", ["assets/icons/**", "assets/logo.png"], []),
                               ("auth-api", "developer", ["src/api/**"], [])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_only_screen_designers_are_named_when_mixed_with_asset_designers(self):
        asset = ("app-icons", "designer", ["assets/icons/**"], [])
        screen = ("login-design", "designer", ["docs/design/screens/login/**", "assets/login/**"], [])
        write_plan(self.root, [asset, screen])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("(지금: 없음)", proc.stderr)
        write_plan(self.root, [("design-direction", "director", DIRECTOR_OWNS, []), asset, screen])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("login-design: designer 태스크는 director 태스크(design-direction)에", proc.stderr)
        self.assertNotIn("app-icons", proc.stderr)

    def test_plan_without_designers_needs_no_director(self):
        write_plan(self.root, [("auth-api", "developer", ["src/api/**"], [])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_config_without_director_skips_rule(self):
        self.remove_role("director")
        write_plan(self.root, [("login-design", "designer", ["docs/design/screens/login/**"], [])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_append_checks_only_new_designer_tasks(self):
        """0.3.0 에서 적용한 designer 태스크는 고칠 수 없으므로 --append 규칙은 새 designer 만 본다."""
        config = self.root / ".orchestration/config.toml"
        original = config.read_text(encoding="utf-8")
        self.remove_role("director")
        old = [("login-design", "designer", ["docs/design/screens/login/**"], []),
               ("auth-api", "developer", ["src/api/**"], [])]
        write_plan(self.root, old)
        self.orch("plan", "apply")
        config.write_text(original, encoding="utf-8")  # 0.4.0 으로 올리며 [roles.director] 를 더했다
        write_plan(self.root, old + [("auth-ui", "developer", ["src/ui/**"], ["auth-api"])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        signup = ("signup-design", "designer", ["docs/design/screens/signup/**"], [])
        write_plan(self.root, old + [signup])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("(지금: 없음)", proc.stderr)
        write_plan(self.root, old + [("design-direction", "director", DIRECTOR_OWNS, []),
                                     ("signup-design", "designer", ["docs/design/screens/signup/**"], ["design-direction"])])
        proc = self.check_cmd()
        self.assertEqual(proc.returncode, 0, proc.stderr)


class AssetOnlyTest(unittest.TestCase):
    """에셋 전용 판정은 닫힌 쪽으로 실패한다. owns 가 비면(태스크·config 모두) 에셋 전용이 아니다(director 규칙에 남는다)."""

    @staticmethod
    def task(owns):
        return plan.PlanTask("icons", "designer", "아이콘", owns, [], "t", "c", "k", ["a"])

    def test_empty_owns_is_not_asset_only(self):
        no_owns = Config({"roles": {"designer": {"title": "디자인"}}})
        assets = Config({"roles": {"designer": {"owns": ["assets/**"]}}})
        self.assertFalse(plan.asset_only(self.task([]), no_owns))
        self.assertFalse(plan.asset_only(self.task([]), Config({"roles": {"designer": {"owns": []}}})))
        self.assertTrue(plan.asset_only(self.task([]), assets))  # 태스크 owns 가 없으면 config 기본 owns 로 본다
        self.assertTrue(plan.asset_only(self.task(["assets/icons/**", "assets/logo.png"]), no_owns))
        self.assertFalse(plan.asset_only(self.task(["assets/icons/**", "docs/design/screens/x/**"]), assets))


class DirectorFlowTest(TaskCase):
    """가짜 orca·Codex 로 디자인 디렉터 한 바퀴: plan apply → director 시작 → ask-user → 메인 answer → 리뷰 → accept(done) →
    화면 designer 와 UI 기반 developer 가 함께 시작한다."""

    GRAPH = [("design-direction", "director", DIRECTOR_OWNS, []),
             ("login-design", "designer", ["docs/design/screens/login/**"], ["design-direction"]),
             ("ui-foundation", "developer", ["src/ui/base/**"], ["design-direction"]),
             ("auth-api", "developer", ["src/api/**"], []),
             ("login-ui", "developer", ["src/ui/login/**"], ["login-design", "ui-foundation"])]
    QUESTION = {"question": "어느 스타일 묶음이 이 서비스에 맞나요?", "example": "차분한 모노톤이면 정보가 많은 화면도 정돈돼 보인다",
                "context": "레퍼런스 보드: https://claude.ai/artifact/board-1",
                "options": [{"label": "차분한 모노톤", "description": "회색 위계와 강조색 하나"},
                            {"label": "선명한 포인트", "description": "브랜드 색을 넓게 쓴다"}],
                "recommended": 0, "reason": "정보 위계가 잘 보인다", "header": "레퍼런스"}

    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        write_plan(self.root, self.GRAPH)
        self.git("add", "--", plan.PLAN_FILE)
        self.git("commit", "-q", "-m", "[T-000] docs: 태스크 그래프")

    def commit_work(self, task_id, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"[{task_id}] feat: {path}")

    def test_director_flow_round_trip(self):
        applied = json.loads(self.orch("plan", "apply").stdout)
        self.assertEqual({t["key"]: t["id"] for t in applied["tasks"]},
                         {"design-direction": "T-001", "login-design": "T-002", "ui-foundation": "T-003",
                          "auth-api": "T-004", "login-ui": "T-005"})
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001", "T-004"])
        self.assertEqual({w["id"]: w["reason"] for w in out["waiting"]}, {"T-002": "deps", "T-003": "deps", "T-005": "deps"})

        question = self.tmp / "director-q.json"
        question.write_text(json.dumps(self.QUESTION, ensure_ascii=False), encoding="utf-8")
        worker = {"ORCA_TERMINAL_HANDLE": "term_w1", "ORCA_TEAM_ASK_WAIT": "0.2"}
        proc = self.orch("ask-user", "--task", "T-001", "--json", str(question), check=False, env=worker)
        self.assertEqual(proc.returncode, 4, proc.stderr)
        out_id = json.loads(proc.stdout)["id"]
        status = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual(status["open_questions"], [{"id": out_id, "type": "ask", "task": "T-001"}])
        self.orch("answer", out_id, "차분한 모노톤 (추천)", env=MAIN)
        got = json.loads(self.orch("ask-user", "--task", "T-001", "--wait", out_id, env=worker).stdout)
        self.assertEqual(got, {"id": out_id, "answer": "차분한 모노톤 (추천)"})

        self.commit_work("T-001", "docs/design/direction.md",
                         "# 디자인 방향\n\ndesign_system_url: https://claude.ai/artifact/ds-1\n")
        self.commit_work("T-001", "docs/design/system/tokens.json", '[{"name": "color.primary", "value": "#1F6F5C"}]\n')
        self.commit_work("T-001", "docs/tasks/T-001/report.md", "## r1\n- https://claude.ai/artifact/ds-1\n")
        self.assertEqual(json.loads(self.orch("review", "T-001").stdout)["verdict"], "APPROVE")
        prompt = sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")
        self.assertIn(".orchestration/design-rules.md", prompt)
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "done")

        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-002", "T-003"])
        self.assertEqual([(w["id"], w["reason"]) for w in out["waiting"]], [("T-005", "deps")])


if __name__ == "__main__":
    unittest.main()
