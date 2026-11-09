import json
import os
import subprocess
import sys
import time
import unittest

from helpers import FAKES, PLUGIN, ProjectCase

HOOK = PLUGIN / "hooks" / "orca_team_hook.py"
LIVE = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}
STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}


class HookCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.drive_alive()  # 관제가 살아 있어야 메인 훅이 orch up 을 분리 프로세스로 띄우지 않는다
        self.respond("terminal show", LIVE)

    def hook(self, event, payload, cwd=None, env=None):
        body = json.dumps({"cwd": str(cwd or self.root), **payload})
        base = {k: v for k, v in self.env.items() if k != "ORCA_TERMINAL_HANDLE"}
        proc = subprocess.run([sys.executable, str(HOOK), event], input=body, capture_output=True, text=True,
                              env={**base, **(env or {})})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def mark(self, session_id, prompt):
        self.hook("role-mark", {"session_id": session_id, "prompt": prompt})

    def write(self, session_id, path):
        return self.hook("guard", {"session_id": session_id, "tool_name": "Write", "tool_input": {"file_path": path}})

    def tool(self, session_id, name, tool_input=None):
        return self.hook("guard", {"session_id": session_id, "tool_name": name, "tool_input": tool_input or {}})

    @staticmethod
    def decision(out):
        return ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


class RoleMarkTest(HookCase):
    def role(self, session_id):
        return (self.root / f".orchestration/state/sessions/{session_id}").read_text(encoding="utf-8").strip()

    def test_roles_from_first_prompt(self):
        self.mark("s-main", "안녕")
        self.mark("s-w", "Your task ID is: task_1")
        self.assertEqual([self.role(s) for s in ("s-main", "s-w")], ["main", "worker"])

    def test_first_prompt_wins(self):
        self.mark("s1", "안녕")
        self.mark("s1", "안녕 다시")
        self.assertEqual(self.role("s1"), "main")

    def test_worker_marker_follows_each_preamble(self):
        self.mark("s-w", "Your task ID is: t1\n너는 orca-team 의 개발(developer) 워커다.")
        self.assertEqual(self.role("s-w"), "worker")
        self.mark("s-w", "Your task ID is: t2\n너는 orca-team 의 기획(planner) 워커다.")
        self.assertEqual(self.role("s-w"), "worker:planner")
        self.mark("s-main", "안녕")
        self.mark("s-main", "Your task ID is: t3")
        self.assertEqual(self.role("s-main"), "main")

    def test_agent_terminal_is_worker_without_marker(self):
        (self.root / ".orchestration/state/agents.json").write_text(
            json.dumps({"production": {"handle": "term_a1"}}), encoding="utf-8")
        env = {"ORCA_TERMINAL_HANDLE": "term_a1"}
        self.assertIsNone(self.hook("session-start", {"session_id": "after-clear", "source": "clear"}, env=env))
        out = self.hook("guard", {"session_id": "after-clear", "tool_name": "Write",
                                  "tool_input": {"file_path": str(self.root / "src/a.ts")}}, env=env)
        self.assertIsNone(self.decision(out))
        self.assertIsNone(self.hook("role-mark", {"session_id": "agent-mark", "prompt": "안녕"}, env=env))
        self.assertEqual(self.role("agent-mark"), "worker")


class GuardTest(HookCase):
    def test_main_blocked_from_product_files_after_setup(self):
        self.mark("m", "hi")
        self.orch("setup-done")
        self.assertEqual(self.decision(self.write("m", str(self.root / "src/app.ts"))), "deny")
        self.assertEqual(self.decision(self.write("unknown-session", str(self.root / "src/app.ts"))), "deny")
        for allowed in (".orchestration/config.toml", "CLAUDE.md", "AGENTS.md", ".claude/settings.json"):
            self.assertIsNone(self.write("m", str(self.root / allowed)), allowed)
        self.assertIsNone(self.write("m", str(self.tmp / "outside.txt")))

    def test_main_can_write_during_setup_mode(self):
        self.mark("m", "hi")
        self.assertIsNone(self.write("m", str(self.root / "package.json")))

    def test_main_cannot_call_orca_orchestration(self):
        self.mark("m", "hi")
        out = self.tool("m", "Bash", {"command": "orca orchestration run-create --objective x"})
        self.assertEqual(self.decision(out), "deny")
        self.assertEqual(self.decision(self.tool("m", "Bash", {"command": "cd x && orca orchestration check"})), "deny")
        self.assertIsNone(self.tool("m", "Bash", {"command": '.orchestration/bin/orch start T-001'}))

    def test_main_cannot_call_orca_orchestration_or_run_drive(self):
        self.mark("m", "안녕")
        for command in ("orca orchestration send --to run:x", ".orchestration/bin/orch drive", "cd x && orch drive"):
            with self.subTest(command=command):
                out = self.tool("m", "Bash", {"command": command})
                self.assertEqual(self.decision(out), "deny")
        self.assertIn("관제", self.tool("m", "Bash", {"command": "orca orchestration check"})
                      ["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(self.tool("m", "Bash", {"command": ".orchestration/bin/orch drop T-001 --reason x"}))

    def test_main_quoted_orch_drive_and_orca_orchestration_are_denied(self):
        self.mark("m", "안녕")
        for command in ('bash -c "orch drive"', "sh -c 'orca orchestration check'", '"/x/bin/orch" drive',
                         "/x/bin/orch drive"):
            with self.subTest(command=command):
                self.assertEqual(self.decision(self.tool("m", "Bash", {"command": command})), "deny")

    def test_main_absolute_path_orca_orchestration_is_denied(self):
        self.mark("m", "안녕")
        for command in ("/opt/homebrew/bin/orca orchestration worker-list", "cd x && /usr/local/bin/orca orchestration check"):
            with self.subTest(command=command):
                self.assertEqual(self.decision(self.tool("m", "Bash", {"command": command})), "deny")

    def test_main_lookalike_commands_are_allowed(self):
        self.mark("m", "안녕")
        for command in (".orchestration/bin/orch drive-x", "/x/bin/orch drive-x", "/opt/bin/orca orchestrations",
                        ".orchestration/bin/orch driver-status", ".orchestration/bin/orch status",
                        ".orchestration/bin/orch start T-001", 'echo orca orchestrations'):
            with self.subTest(command=command):
                self.assertIsNone(self.tool("m", "Bash", {"command": command}))

    def test_worker_rules(self):
        self.mark("w", "Your task ID is: t")
        self.assertIsNone(self.write("w", str(self.root / "src/app.ts")))
        self.assertEqual(self.decision(self.tool("w", "AskUserQuestion")), "deny")

    @staticmethod
    def reason(out):
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def test_ask_user_question_deny_points_to_the_right_command(self):
        self.mark("w", "Your task ID is: t\n너는 orca-team 의 개발(developer) 워커다. 아래 태스크 하나만 수행한다.")
        worker = self.reason(self.tool("w", "AskUserQuestion"))
        self.assertIn("프리앰블의 ask 명령", worker)
        self.assertNotIn("orch ask-user", worker)
        self.mark("p", "Your task ID is: t\n너는 orca-team 의 기획(planner) 워커다. 아래 태스크 하나만 수행한다.")
        planner = self.reason(self.tool("p", "AskUserQuestion"))
        self.assertIn(".orchestration/bin/orch ask-user --task", planner)
        self.assertIsNone(self.write("p", str(self.root / "docs/planning/spec.md")))

    def test_director_worker_is_marked_and_pointed_to_ask_user(self):
        self.mark("d", "Your task ID is: t\n너는 orca-team 의 디자인 디렉터(director) 워커다. 아래 태스크 하나만 수행한다.")
        marker = (self.root / ".orchestration/state/sessions/d").read_text(encoding="utf-8").strip()
        self.assertEqual(marker, "worker:director")
        reason = self.reason(self.tool("d", "AskUserQuestion"))
        self.assertTrue(reason.startswith("디자인 디렉터 워커는 AskUserQuestion 을 쓰지 않는다."), reason)
        self.assertIn(".orchestration/bin/orch ask-user --task T-xxx --json -", reason)
        self.mark("p", "Your task ID is: t\n너는 orca-team 의 기획(planner) 워커다. 아래 태스크 하나만 수행한다.")
        self.assertTrue(self.reason(self.tool("p", "AskUserQuestion")).startswith("기획 워커는 AskUserQuestion 을 쓰지 않는다."))
        self.assertIsNone(self.write("d", str(self.root / "docs/design/direction.md")))
        self.assertIsNone(self.hook("session-start", {"session_id": "p"}))

    def test_ask_worker_mark_matches_worker_spec_template(self):
        import importlib.util

        from orch import ask
        from orch.util import render

        spec = importlib.util.spec_from_file_location("orca_team_hook_m2", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        template = (PLUGIN / "templates/worker-spec.md").read_text(encoding="utf-8")
        self.assertEqual(set(module.ASK_WORKER_TITLES), {f"worker:{role}" for role in ask.ASK_ROLES})
        for role, title in (("planner", "기획"), ("director", "디자인 디렉터")):
            found = module.ASK_WORKER_RE.search(render(template, {"role_title": title, "role": role}))
            self.assertEqual(found.group(1) if found else None, role)
        for role, title in (("developer", "개발"), ("designer", "디자인")):
            self.assertNotRegex(render(template, {"role_title": title, "role": role}), module.ASK_WORKER_RE)

    def test_noop_outside_orca_team_project(self):
        other = self.tmp / "other"
        other.mkdir()
        out = self.hook("guard", {"session_id": "m", "tool_name": "Write",
                                  "tool_input": {"file_path": str(other / "a.txt")}}, cwd=other)
        self.assertIsNone(out)


class StopAndSessionStartTest(HookCase):
    def setUp(self):
        super().setUp()
        self.orch("setup-done")
        self.mark("m", "hi")

    def test_stop_blocks_main_without_watcher(self):
        out = self.hook("stop", {"session_id": "m", "stop_hook_active": False})
        self.assertEqual(out["decision"], "block")
        self.assertIn("orch watch", out["reason"])

    def test_stop_allows_when_watcher_alive_or_reentry(self):
        pid_file = self.root / ".orchestration/state/watch.pid"
        pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.assertIsNone(self.hook("stop", {"session_id": "m"}))
        pid_file.unlink()
        self.assertIsNone(self.hook("stop", {"session_id": "m", "stop_hook_active": True}))

    def test_stop_needs_run_id(self):
        run_json = self.root / ".orchestration/state/run.json"
        run_json.write_text("{}", encoding="utf-8")
        self.assertIsNone(self.hook("stop", {"session_id": "m"}))
        run_json.write_text('{"run_id": "run_x"}', encoding="utf-8")
        out = self.hook("stop", {"session_id": "m"})
        self.assertEqual(out["decision"], "block")

    def test_session_start_for_main_mentions_up_and_unread(self):
        self.orch("notify", "--type", "progress", "x")
        out = self.hook("session-start", {"session_id": "fresh", "source": "startup"})
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/orca-team:up", context)
        self.assertIn("1건", context)
        self.assertFalse((self.root / ".orchestration/state/auto-up.pid").exists())  # 관제가 살아 있으면 띄우지 않는다

    def test_session_start_shows_old_config_needs_upgrade(self):
        (self.root / ".orchestration/config.toml").write_text("[coordinator]\nauto_up = true\n", encoding="utf-8")
        context = self.hook("session-start", {"session_id": "fresh", "source": "startup"})[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("orch upgrade", context)
        self.assertNotIn("자동으로 다시 띄운다", context)

    def test_session_start_mentions_open_questions(self):
        from orch import comm
        from orch.paths import Project

        comm.post_decide(Project(self.root), "worker_failed", "T-001", "실패")
        (self.root / ".orchestration/state/outbox.cursor").write_text("1", encoding="utf-8")
        context = self.hook("session-start", {"session_id": "fresh"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("답하지 않은 질문이 1건", context)
        self.assertIn("orch reask", context)

    def test_session_start_fails_open_when_open_question_count_breaks(self):
        import importlib.util
        from unittest import mock

        from orch import ask
        from orch.paths import Project

        spec = importlib.util.spec_from_file_location("orca_team_hook_test", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = {k: v for k, v in os.environ.items() if k != "ORCA_TERMINAL_HANDLE"}
        env.update({"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(ask, "open_questions", side_effect=RuntimeError("boom")):
            out = module.session_start(Project(self.root), {"session_id": "fresh"})
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/orca-team:up", context)
        self.assertNotIn("답하지 않은 질문", context)

    def test_session_start_silent_for_marked_worker(self):
        self.mark("w", "Your task ID is: t")
        self.assertIsNone(self.hook("session-start", {"session_id": "w", "source": "resume"}))


class PluginRootRecordTest(HookCase):
    def setUp(self):
        super().setUp()
        self.marker().unlink(missing_ok=True)  # scaffold 가 적은 표시를 지워 훅만 검증한다

    def marker(self):
        return self.root / ".orchestration/state/plugin-root"

    def test_session_start_records_plugin_root_even_for_worker(self):
        self.mark("w", "Your task ID is: t")
        self.assertIsNone(self.hook("session-start", {"session_id": "w", "source": "startup"}))
        self.assertEqual(self.marker().read_text(encoding="utf-8").strip(), str(PLUGIN.resolve()))

    def test_session_start_records_plugin_root_for_main_too(self):
        self.hook("session-start", {"session_id": "fresh", "source": "startup"})
        self.assertEqual(self.marker().read_text(encoding="utf-8").strip(), str(PLUGIN.resolve()))

    def test_unwritable_state_does_not_break_hook(self):
        self.marker().mkdir(parents=True)  # 파일 자리에 디렉터리를 둬 쓰기를 실패시킨다
        out = self.hook("session-start", {"session_id": "fresh", "source": "startup"})
        self.assertIn("/orca-team:up", out["hookSpecificOutput"]["additionalContext"])

    def test_no_project_writes_nothing(self):
        other = self.tmp / "plain"
        other.mkdir()
        self.assertIsNone(self.hook("session-start", {"session_id": "x"}, cwd=other, env={"CLAUDE_PROJECT_DIR": str(other)}))
        self.assertFalse((other / ".orchestration").exists())


AGENT_ENV = {"ORCA_TERMINAL_HANDLE": "term_agent"}


class EnvAgentTest(HookCase):
    def setUp(self):
        super().setUp()
        self.orch("setup-done")

    def test_session_start_main_context_has_main_session_only_disclaimer(self):
        self.drive_alive()
        out = self.hook("session-start", {"session_id": "fresh"})
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("메인 세션에만", context)
        self.assertIn("/orca-team:up", context)

    def test_session_start_main_context_when_env_handle_differs(self):
        out = self.hook("session-start", {"session_id": "fresh"}, env={"ORCA_TERMINAL_HANDLE": "term_other"})
        self.assertIn("메인 세션에만", out["hookSpecificOutput"]["additionalContext"])



class MalformedInputTest(HookCase):
    def run_raw(self, event, data: bytes):
        proc = subprocess.run([sys.executable, str(HOOK), event], input=data, capture_output=True, env=self.env,
                              cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")

    def test_malformed_stdin_passes_for_every_subcommand(self):
        for event in ("role-mark", "guard", "session-start", "stop"):
            for data in (b"[]", b'{"cwd": 5}', b"\xff\xfe\x80", b"{not json"):
                with self.subTest(event=event, data=data):
                    self.run_raw(event, data)


class SessionIdTest(HookCase):
    def test_path_traversal_session_id_writes_nothing_outside_sessions(self):
        before = {p for p in self.tmp.rglob("*")}
        for sid in ("../x", "a/../b", "..", "a b"):
            self.mark(sid, "hi")
        sessions = self.root / ".orchestration/state/sessions"
        self.assertFalse((self.root / ".orchestration/state/x").exists())
        self.assertEqual({p for p in self.tmp.rglob("*")} - before - set(sessions.rglob("*")) - {sessions}, set())
        self.assertEqual(list(sessions.glob("*")) if sessions.exists() else [], [])

    def test_bad_session_id_falls_back_to_env_check_or_main(self):
        cmd = {"command": "orca orchestration worker-list"}
        self.orch("setup-done")
        self.assertEqual(self.decision(self.hook("guard", {"session_id": "../x", "tool_name": "Bash",
                                                           "tool_input": cmd})), "deny")
        (self.root / ".orchestration/state/agents.json").write_text(
            json.dumps({"production": {"handle": "term_agent"}}), encoding="utf-8")
        self.assertIsNone(self.hook("guard", {"session_id": "../x", "tool_name": "Bash", "tool_input": cmd},
                                    env=AGENT_ENV))
        self.assertIsNone(self.hook("session-start", {"session_id": "../x"}, env=AGENT_ENV))


class StateAndAllowlistGuardTest(HookCase):
    def edit(self, session_id, rel, tool="Write", env=None):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        return self.hook("guard", {"session_id": session_id, "tool_name": tool,
                                   "tool_input": {key: str(self.root / rel)}}, env=env)

    def test_state_edits_denied_for_every_role_even_in_setup_mode(self):
        self.mark("m", "hi")
        self.mark("w", "Your task ID is: t")
        self.assertTrue((self.root / ".orchestration/state/setup-mode").exists())
        (self.root / ".orchestration/state/agents.json").write_text(
            json.dumps({"production": {"handle": "term_agent"}}), encoding="utf-8")
        for session_id, env in (("m", None), ("w", None), ("c", AGENT_ENV)):
            for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                with self.subTest(session=session_id, tool=tool):
                    out = self.edit(session_id, ".orchestration/state/tasks/T-001.json", tool, env)
                    self.assertEqual(self.decision(out), "deny")
        self.assertEqual(self.decision(self.edit("w", ".orchestration/state", env=None)), "deny")

    def test_non_dict_run_json_is_treated_as_empty(self):
        from orch import util
        from orch.paths import Project
        run_json = self.root / ".orchestration/state/run.json"
        run_json.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(util.load_run(Project(self.root)), {})
        self.orch("setup-done")
        out = self.hook("guard", {"session_id": "s", "tool_name": "Bash",
                                  "tool_input": {"command": "orca orchestration check"}})
        self.assertEqual(self.decision(out), "deny")
        self.assertFalse((self.root / ".orchestration/state/hook-errors.log").exists())


CONTEXT = ("## 기획 핵심\n- 할 일 웹앱(정적 HTML), 저장은 localStorage\n- 삭제는 확인 없이 즉시\n\n"
           "## 자동 결정\n- 글자 수 200자, 지원 브라우저는 최신 Chrome·Safari\n\n"
           "| key | 직무 | 선행 |\n|---|---|---|\n| todo-design | designer | - |\n| todo-ui | developer | todo-design |")
# 같은 내용을 제목 수준·목록 기호·굵게·표 여백·줄바꿈만 바꿔 다시 그린 것
RERENDERED = ("아래는 기획 요약입니다.\n\n### 기획 핵심\n\n* 할 일 웹앱(정적 HTML), 저장은 **localStorage**\n"
              "* 삭제는 확인 없이   즉시\n\n### 자동 결정\n> 글자 수 200자, 지원 브라우저는 최신 Chrome·Safari\n\n"
              "|key|직무|선행|\n|---|---|---|\n|todo-design|designer|-|\n|todo-ui|developer|todo-design|\n")
CONFIRM = "이 기획으로 확정할까요?"


class MainContextGuardTest(HookCase):
    """render.context 가 있는 열린 질문은 메인이 그 context 를 먼저 평문으로 보여 줘야 AskUserQuestion 이 통과한다(F1)."""

    def setUp(self):
        super().setUp()
        self.orch("setup-done")
        self.mark("m", "hi")
        self.transcript = self.tmp / "main.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        planner = {"id": "T-001", "role": "planner", "title": "기획", "status": "working", "round": 1,
                   "owns": ["docs/planning/**"], "history": []}
        (self.root / ".orchestration/state/tasks/T-001.json").write_text(json.dumps(planner), encoding="utf-8")

    def post(self, kind="ask", question=CONFIRM, context=CONTEXT):
        """기획 워커(ask)·코디네이터(question·approval)가 올린 render 붙은 질문. outbox 줄을 돌려준다."""
        from orch import ask, comm
        from orch.paths import Project

        data = {"question": question, "example": "전체 문서는 docs/planning/ 에 있다",
                "options": [{"label": "확정", "description": "이대로 진행"},
                            {"label": "수정할 부분 있음", "description": "고칠 점을 말한다"}],
                "recommended": 0, "reason": "점검표를 모두 채웠다."}
        if context is not None:
            data["context"] = context
        q = ask.validate_question(data)
        extra = {"render": ask.build_render(q), **({"task": "T-001"} if kind == "ask" else {})}
        return comm.post(Project(self.root), kind, q["question"], extra)

    def append(self, entry):
        with self.transcript.open("a", encoding="utf-8") as fh:
            fh.write((entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)) + "\n")

    def say(self, text):
        self.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    def ask_user(self, *questions, session="m", env=None, transcript=True):
        tool_input = {"questions": [{"question": q, "header": "기획", "multiSelect": False,
                                     "options": [{"label": "확정 (추천)", "description": "d"},
                                                 {"label": "수정할 부분 있음", "description": "d"}]} for q in questions]}
        payload = {"session_id": session, "tool_name": "AskUserQuestion", "tool_input": tool_input}
        if transcript is True:
            payload["transcript_path"] = str(self.transcript)
        elif transcript is not None:
            payload["transcript_path"] = transcript
        return self.hook("guard", payload, env=env)

    def denied_ids(self):
        path = self.root / ".orchestration/state/context-denied.json"
        return json.loads(path.read_text(encoding="utf-8"))["ids"] if path.exists() else []

    @staticmethod
    def reason(out):
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def test_unshown_context_is_denied_with_the_context_itself(self):
        entry = self.post()
        # watch 출력(도구 결과)에 context 가 있어도, 요약만 말해도 보여 준 것이 아니다
        self.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(entry, ensure_ascii=False)}]}})
        self.say("기획 요약: 할 일 웹앱, localStorage 저장, 태스크 2개입니다.")
        out = self.ask_user(entry["render"]["question"])
        self.assertEqual(self.decision(out), "deny")
        reason = self.reason(out)
        self.assertIn(CONTEXT, reason)
        self.assertIn(entry["id"], reason)
        self.assertIn("그대로", reason)
        self.assertIn("AskUserQuestion", reason)
        self.assertEqual(self.denied_ids(), [entry["id"]])

    def test_context_shown_with_other_whitespace_and_markdown_is_allowed(self):
        for kind in ("ask", "approval", "question"):
            with self.subTest(kind=kind):
                entry = self.post(kind, question=f"{kind} 질문으로 확정할까요?")
                self.say(RERENDERED)
                self.assertIsNone(self.ask_user(entry["render"]["question"]))
        self.assertEqual(self.denied_ids(), [])

    def test_second_attempt_on_the_same_id_is_allowed(self):
        entry = self.post()
        question = entry["render"]["question"]
        self.assertEqual(self.decision(self.ask_user(question)), "deny")
        self.assertIsNone(self.ask_user(question))  # transcript 기록이 늦어도 메인이 거부에 갇히지 않는다
        self.assertIsNone(self.ask_user(question))
        self.assertEqual(self.denied_ids(), [entry["id"]])
        other = self.post("approval", question="두 번째 질문으로 갈까요?")
        self.assertEqual(self.decision(self.ask_user(other["render"]["question"])), "deny")
        self.assertEqual(self.denied_ids(), [entry["id"], other["id"]])

    def test_allows_unmatched_contextless_and_answered_questions(self):
        self.assertIsNone(self.ask_user(CONFIRM))  # 열린 질문 없음
        entry = self.post()
        self.assertIsNone(self.ask_user("전혀 다른 질문인가요?"))
        bare = self.post("question", question="컨텍스트 없는 질문인가요?", context=None)
        self.assertNotIn("context", bare["render"])
        self.assertIsNone(self.ask_user(bare["render"]["question"]))
        self.orch("answer", entry["id"], "확정", env={"ORCA_TERMINAL_HANDLE": "term_main"})
        self.assertIsNone(self.ask_user(entry["render"]["question"]))
        self.assertEqual(self.denied_ids(), [])

    def test_worker_deny_messages_are_unchanged(self):
        self.mark("w", "Your task ID is: t\n너는 orca-team 의 개발(developer) 워커다.")
        self.mark("p", "Your task ID is: t\n너는 orca-team 의 기획(planner) 워커다.")
        question = f"{CONFIRM}\n예: 전체 문서는 docs/planning/ 에 있다"
        before = {s: self.reason(self.ask_user(question, session=s)) for s in ("w", "p")}
        self.post()
        after = {s: self.reason(self.ask_user(question, session=s)) for s in ("w", "p")}
        self.assertEqual(after, before)
        for reason in after.values():
            self.assertNotIn(CONTEXT, reason)
        self.assertIn("프리앰블의 ask 명령", after["w"])
        self.assertIn("메인", after["w"])
        self.assertEqual(self.denied_ids(), [])

    def test_fails_open_on_transcript_problems(self):
        entry = self.post()
        question = entry["render"]["question"]
        self.assertIsNone(self.ask_user(question, transcript=None))  # transcript_path 없음
        self.assertIsNone(self.ask_user(question, transcript=5))
        self.assertIsNone(self.ask_user(question, transcript=str(self.tmp / "missing.jsonl")))
        self.assertIsNone(self.ask_user(question, transcript=str(self.tmp)))  # 파일이 아니다
        self.say("앞 줄은 멀쩡하다")
        self.append('{"type": "assistant", "message": {"content": [')  # 깨진 줄
        self.assertIsNone(self.ask_user(question))
        self.assertEqual(self.denied_ids(), [])

    def test_line_separator_char_in_tool_result_does_not_disable_guard(self):
        entry = self.post()
        # Claude Code 는 U+2028 을 이스케이프하지 않는다. splitlines() 는 이것으로 JSON 한 줄을 둘로 쪼갠다
        self.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "앞\u2028뒤\u2029끝\u0085"}]}})
        self.assertIn("\u2028", self.transcript.read_text(encoding="utf-8"))
        self.say("기획 요약만 말했다.")
        self.assertEqual(self.decision(self.ask_user(entry["render"]["question"])), "deny")

    def test_context_shown_beyond_the_tail_line_bound_is_denied(self):
        entry = self.post()
        self.say(RERENDERED)
        for i in range(400):
            self.say(f"잡담 {i}")
        self.assertEqual(self.decision(self.ask_user(entry["render"]["question"])), "deny")

    def test_corrupt_denied_file_denies_once_then_recovers(self):
        entry = self.post()
        question = entry["render"]["question"]
        path = self.root / ".orchestration/state/context-denied.json"
        for junk in ("{not json", "[1, 2]", '{"ids": "x"}', '{"ids": [1, null]}'):
            with self.subTest(junk=junk):
                path.write_text(junk, encoding="utf-8")
                self.assertEqual(self.decision(self.ask_user(question)), "deny")
                self.assertEqual(self.denied_ids(), [entry["id"]])  # 파일을 다시 써서 거부 루프를 끊는다
                self.assertIsNone(self.ask_user(question))
                path.unlink()

    def test_fails_open_when_question_lookup_breaks(self):
        entry = self.post()
        (self.root / ".orchestration/state/tasks/T-001.json").write_text("[1]", encoding="utf-8")
        self.assertIsNone(self.ask_user(entry["render"]["question"]))
        self.assertEqual(self.denied_ids(), [])


class AutoUpHookTest(HookCase):
    """메인 세션 훅이 죽은 관제를 다시 띄운다. 세팅이 끝난 프로젝트, 가짜 orca."""

    def setUp(self):
        super().setUp()
        self.p_drive_pid().unlink()  # 이 클래스는 죽은 관제에서 시작한다
        self.orch("setup-done")

    def context(self, out):
        return out["hookSpecificOutput"]["additionalContext"] if out else None

    def wait_spawned(self):
        from orch.util import process_alive

        pid = int((self.root / ".orchestration/state/auto-up.pid").read_text(encoding="utf-8").split()[0])
        deadline = time.monotonic() + 20
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(process_alive(pid))

    def p_drive_pid(self):
        return self.root / ".orchestration/state/drive/drive.pid"

    def run_state(self):
        return json.loads((self.root / ".orchestration/state/run.json").read_text(encoding="utf-8"))

    def test_prompt_revives_dead_drive(self):
        self.respond("terminal show", STALE, code=1)  # 옛 관제 터미널은 사라졌다
        self.respond("terminal create", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        out = self.hook("role-mark", {"session_id": "m", "prompt": "할 일 앱 만들어 줘"})
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("자동으로 다시 띄우는 중", self.context(out))
        self.wait_spawned()
        self.assertEqual(self.run_state()["drive_handle"], "term_new")

    def test_prompt_within_interval_skips(self):
        self.drive_alive()
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "hi"}))
        self.p_drive_pid().unlink()
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "again"}))
        self.assertFalse((self.root / ".orchestration/state/auto-up.pid").exists())

    def test_session_start_checks_regardless_of_interval(self):
        self.drive_alive()
        self.hook("role-mark", {"session_id": "m", "prompt": "hi"})
        self.p_drive_pid().unlink()
        self.respond("terminal create", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        context = self.context(self.hook("session-start", {"session_id": "fresh", "source": "startup"}))
        self.assertIn("자동으로 다시 띄우는 중", context)
        self.wait_spawned()

    def test_worker_sessions_skip(self):
        self.assertIsNone(self.hook("role-mark", {"session_id": "w", "prompt": "Your task ID is: t"}))
        self.assertEqual(self.calls_for("terminal create"), [])
        self.assertFalse((self.root / ".orchestration/state/auto-up.pid").exists())

    def test_disabled_reports_without_launch(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("auto_up = true", "auto_up = false"), encoding="utf-8")
        self.assertIn("auto_up = false", self.context(self.hook("role-mark", {"session_id": "m", "prompt": "hi"})))
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_setup_mode_skips(self):
        (self.root / ".orchestration/state/setup-mode").write_text("", encoding="utf-8")
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "hi"}))
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_auto_up_error_does_not_block(self):
        (self.root / ".orchestration/state/auto-up.json").mkdir()
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "hi"}))
        context = self.context(self.hook("session-start", {"session_id": "fresh", "source": "startup"}))
        self.assertIn("/orca-team:up", context)
        self.assertIn("auto-up", (self.root / ".orchestration/state/hook-errors.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
