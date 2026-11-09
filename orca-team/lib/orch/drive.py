"""관제(orch drive). 관제 탭의 셸에서 돌며 Run 을 갖는다.

메인이 넣은 요청(queue.py)을 실행하고, Orca 메일·리뷰 자식 프로세스를 돌리며 정해진 절차를 실행한다. 판단이 필요한
일은 outbox decide 로 메인에 올린다(Task 11). 상태는 모두 파일에 있어 다시 띄우면 이어서 한다."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from . import agents, ask, comm, followups, orca, plan, queue, setup, tasks, testgate
from .config import Config
from .globs import matches_any
from .paths import PLUGIN_ROOT, OrchError, Project
from .util import (acquire_pid, git, git_commit_paths, now, pid_alive, read_json, release_pid, update_run,
                   write_json, write_text_atomic)

WAIT_MS = 10000  # 메일을 한 번에 기다리는 시간. 메인 요청 큐도 이 간격으로 본다
IDLE_SECONDS = 1.0
ORCH_BIN = PLUGIN_ROOT / "bin" / "orch"
CHECK_TYPES = "worker_done,escalation,question,status"
RESET_STUCK_SECONDS = 30 * 60  # 인수인계가 이만큼 지나도 안 끝나면 정리한다
RECONCILE_SECONDS = 60  # 메일 없이 끝난 일(죽은 워커, 끊긴 리뷰)을 찾는 간격
UNKNOWN_GRACE_SECONDS = 60  # start_unknown 이 이만큼 지나도 그대로면 복구한다
LOCK_WAIT_TEXT = "테스트 잠금"
TEST_CONFIG_TEXTS = ("`[test]` 명령을 실행할 수 없다", "`[test]` 검증 실패", "tasks.toml` 읽기 실패")


OPEN_JUDGMENTS = ("planning_gaps", "unknown_regression", "token_update")


class Job:
    def __init__(self, kind: str, task_id: str | None, proc):
        self.kind, self.task_id, self.proc = kind, task_id, proc


class Drive:
    def __init__(self, p: Project, cfg: Config, spawn=None, clock=time.monotonic):
        self.p, self.cfg = p, cfg
        self.spawn = spawn or self._spawn
        self.clock = clock
        self.ack: str | None = None
        self.jobs: dict[str, Job] = {}
        self.retry_reviews: set[str] = set()
        self.last_reconcile: float | None = None
        self.failures: dict[str, str] = {}  # 단위별 마지막 실패 문구. 문구가 바뀔 때만 메인에 올린다
        self.check_failed = False
        self.pending_marks: dict[str, tuple[str, object]] = {}  # 리뷰 중 올린 decide 표시(리뷰가 끝난 뒤 쓴다)

    def _spawn(self, args: list[str]):
        """orch 하위 명령을 자식 프로세스로 띄운다. 환경(ORCA_TERMINAL_HANDLE=관제)을 그대로 물려준다."""
        return subprocess.Popen([sys.executable, str(ORCH_BIN), *args], cwd=self.p.root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace")

    def log(self, text: str) -> None:
        line = f"{now()} {text}"
        print(line, flush=True)
        try:
            self.p.drive_dir.mkdir(parents=True, exist_ok=True)
            with self.p.drive_log.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def fail(self, key: str, where: str, exc: Exception, task_id: str | None = None, summary: str | None = None,
             commands=(), extra=None) -> None:
        text = f"{where} 실패: {type(exc).__name__}: {exc}"
        self.log(text)
        if self.failures.get(key) == text:
            return
        try:
            self.decide("escalation", task_id, text[:300] if summary is None else summary[:600],
                        files=self.task_files(task_id), commands=commands, extra=extra)
        except Exception as inner:  # 올리는 일마저 실패해도 관제는 계속 돈다. 키를 남기지 않아 다음 실패가 다시 올린다
            if isinstance(inner, OrchError) and inner.code == 12:
                raise
            self.log(f"{where} 실패를 올리지 못했다: {type(inner).__name__}: {inner}")
            return
        self.failures[key] = text

    def isolated(self, key: str, where: str, fn, task_id: str | None = None, describe=None):
        """한 단위의 일이 예외로 끝나도 관제를 죽이지 않는다. 12(orca 없음)만 올린다.
        describe(exc) 는 올리는 글 {summary, commands, extra} 를 직접 만든다."""
        try:
            result = fn()
        except Exception as exc:
            if isinstance(exc, OrchError) and exc.code == 12:
                raise
            self.fail(key, where, exc, task_id, **(describe(exc) if describe else {}))
            return None
        self.failures.pop(key, None)
        return result

    def beat(self) -> None:
        write_text_atomic(self.p.drive_heartbeat, now())

    def bind(self) -> dict:
        return setup.bind(self.p)

    def run_queue(self) -> int:
        """메인 요청을 넣은 순서로 실행한다. 실행한 수."""
        done = 0
        for path in queue.pending(self.p):
            request = read_json(path, {})
            try:
                result = {"ok": True, "result": self.execute(request)}
            except OrchError as exc:
                if exc.code == 12:
                    raise
                result = {"ok": False, "error": str(exc), "code": exc.code}
            except Exception as exc:  # 잘못된 요청 하나가 관제를 죽이거나 큐에 남지 않게 한다
                result = {"ok": False, "error": f"관제 요청 처리 실패: {type(exc).__name__}: {exc}", "code": 1}
            queue.complete(self.p, path, request, result)
            self.log(f"요청 {request.get('id')} {request.get('command')}: {'완료' if result['ok'] else result['error']}")
            done += 1
            self.beat()
        return done

    def execute(self, request: dict) -> dict:
        command, a = request.get("command"), request.get("args") or {}
        p, cfg = self.p, self.cfg
        if command == "start":
            return tasks.start(p, cfg, a["id"], bool(a.get("retry")), a.get("overrides") or {})
        if command == "start-ready":
            return plan.start_ready(p, cfg)
        if command == "fix":
            return self.fix_with_pending(a["id"], a.get("note"), retry=bool(a.get("retry")),
                                         overrides=a.get("overrides") or {}, force=bool(a.get("force")),
                                         supplement=a.get("supplement"), extra_round=bool(a.get("extra_round")))
        if command == "accept":
            return self.accepted(a["id"], tasks.accept(p, a["id"]))
        if command == "drop":
            return tasks.drop(p, a["id"], a["reason"], bool(a.get("force")))
        if command == "reply":
            orca.call(["orchestration", "reply", "--id", str(a["message_id"]), f"--body={a['body']}"])
            return {"replied": a["message_id"]}
        if command == "design-decide":
            return plan.design_decide(p, cfg, a["id"], list(a.get("approve") or []), dict(a.get("fixes") or {}))
        if command == "scope":
            return tasks.scope(p, a["id"], a["body"])
        if command == "agent-reset":
            return agents.request_reset(p, cfg, a["name"])
        raise OrchError(f"알 수 없는 관제 요청: {command}", 2)

    # --- 메일
    def check(self, wait_ms: int) -> list[dict]:
        args = ["orchestration", "check", "--wait", "--types", CHECK_TYPES, "--timeout-ms", str(wait_ms)]
        if self.ack:
            args += ["--ack", self.ack]
        self.check_failed = True
        try:
            code, data = orca.call(args, check=False, timeout=wait_ms / 1000 + 30)
        except OrchError as exc:
            if exc.code == 12:
                raise
            self.fail("check", "메일 확인", exc)
            return []
        if code != 0 or not data.get("ok"):
            self.fail("check", "메일 확인", OrchError(f"exit {code}: {json.dumps(data, ensure_ascii=False)[:300]}", 1))
            return []
        self.check_failed = False
        self.failures.pop("check", None)
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        self.ack = result.get("deliveryId") or None
        return [m for m in result.get("messages") or [] if isinstance(m, dict)]

    def task_of(self, *refs) -> str | None:
        for ref in refs:
            if ref:
                try:
                    return tasks.which(self.p, str(ref))["id"]
                except OrchError:
                    continue
        return None

    @staticmethod
    def task_files(task_id: str | None) -> list[str]:
        return [f".orchestration/tasks/{task_id}.md", f"docs/tasks/{task_id}/report.md"] if task_id else []

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        dispatch, orca_task = payload.get("dispatchId"), payload.get("taskId")
        body = str(msg.get("body") or msg.get("subject") or "").strip()
        if kind == "worker_done":
            owner = agents.reset_owner(self.p, dispatch)
            if owner:
                out = agents.finish_reset(self.p, owner, payload.get("outcome") == "succeeded")
                self.log(f"{owner} 리셋: {out['status']}")
                if out["status"] == "handoff_failed":
                    self.decide("escalation", None, f"{owner} 에이전트가 인수인계를 쓰지 못했다: {body[:200]}",
                                commands=[f"orch agent reset {owner}"])
                return
            task_id = self.task_of(dispatch, orca_task)
            if not task_id:
                self.log(f"어느 태스크인지 모르는 worker_done: {dispatch}")
                return
            st = tasks.load_state(self.p, task_id) or {}
            if self.stale_dispatch(st, dispatch):
                self.log(f"{task_id} 지난 디스패치의 worker_done 무시: {dispatch}")
                return
            if st.get("agent") in self.cfg.agents:
                agents.release_task(self.p, st["agent"], task_id)
            if payload.get("outcome") == "succeeded":
                self.start_review(task_id)
            else:
                self.decide("worker_failed", task_id, body[:300] or "워커가 실패를 보고했다",
                            files=[f"docs/tasks/{task_id}/report.md"],
                            commands=[f'orch fix {task_id} --note "<원인과 지시>"', f'orch drop {task_id} --reason "<이유>"'])
        elif kind == "question":
            task_id = self.task_of(dispatch, orca_task)
            if task_id and self.stale_dispatch(tasks.load_state(self.p, task_id) or {}, dispatch):
                self.log(f"{task_id} 지난 디스패치의 question 무시: {dispatch}")
                return
            self.decide("worker_question", task_id, body[:300], files=self.task_files(task_id),
                        commands=['orch answer <이 decide 의 id> "<답>"'],
                        extra={"question": body, "message_id": msg.get("id")})
        elif kind == "escalation":
            task_id = self.task_of(dispatch, orca_task)
            if task_id and self.stale_dispatch(tasks.load_state(self.p, task_id) or {}, dispatch):
                self.log(f"{task_id} 지난 디스패치의 escalation 무시: {dispatch}")
                return
            self.decide("escalation", task_id, body[:300] or "워커가 worker_done 없이 멈췄다", files=self.task_files(task_id))

    def msg_task(self, msg: dict) -> str | None:
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        try:
            return self.task_of(payload.get("dispatchId"), payload.get("taskId"))
        except Exception:
            return None

    def message_failure(self, msg: dict, exc: Exception) -> dict:
        """처리하지 못한 메시지를 메인이 복구할 수 있게 올리는 글. Orca 는 메시지를 다시 주지 않는다."""
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        kind, dispatch, orca_task = msg.get("type"), payload.get("dispatchId"), payload.get("taskId")
        body = str(msg.get("body") or msg.get("subject") or "").strip()
        summary = (f"메시지 {msg.get('id')} 처리 실패: {type(exc).__name__}: {exc} "
                   f"[type={kind} taskId={orca_task} dispatchId={dispatch}] {body[:200]}")
        out: dict = {"summary": summary}
        if kind == "question":
            out["extra"] = {"question": body, "message_id": msg.get("id")}
            out["commands"] = ['orch answer <이 decide 의 id> "<답>"']
        return out

    @staticmethod
    def stale_dispatch(st: dict, dispatch) -> bool:
        return bool(dispatch and st.get("dispatch_id") and st["dispatch_id"] != dispatch)

    def decide(self, kind: str, task_id: str | None, summary: str, files=(), commands=(), extra=None) -> dict:
        entry = comm.post_decide(self.p, kind, task_id, summary, files, commands, extra)
        st = tasks.load_state(self.p, task_id) if task_id else None
        if st and f"review:{task_id}" in self.jobs:
            self.pending_marks[task_id] = (entry["id"], st.get("round"))  # 상태는 리뷰 자식이 쓴다. 끝난 뒤 적는다
        elif st:
            st.update(decide=entry["id"], decide_round=st.get("round"))
            tasks.save_state(self.p, st)
        self.log(f"decide {entry['id']} {kind} {task_id or '-'}: {summary[:80]}")
        return entry

    def guard(self, fn, task_id: str | None, note: str | None = None):
        """정해진 절차가 거부되면 메인에 올린다. orca 없음(12)은 올린다. note 는 아직 못 전한 지시(올리는 글에 붙인다)."""
        try:
            return fn()
        except OrchError as exc:
            if exc.code == 12:
                raise
            text = f"관제 절차 실패: {exc}" + (f" (아직 전하지 못한 지시: {note})" if note else "")
            self.decide("escalation", task_id, text[:300] if not note else text[:600], files=self.task_files(task_id))
            return None

    # --- 리뷰
    def start_review(self, task_id: str) -> None:
        key = f"review:{task_id}"
        if key in self.jobs:
            return
        self.jobs[key] = Job("review", task_id, self.spawn(["review", task_id]))
        self.log(f"{task_id} 리뷰 시작")

    def poll_jobs(self) -> None:
        for key, job in list(self.jobs.items()):
            if job.proc.poll() is None:
                continue
            del self.jobs[key]  # 끝난 일은 먼저 뺀다. 뒤 처리가 실패해도 다시 읽지 않는다
            self.isolated(f"job:{key}", f"{key} 마무리", lambda key=key, job=job: self.finish_job(job), job.task_id)

    def finish_job(self, job: Job) -> None:
        out, err = job.proc.communicate()
        if job.kind == "review":
            self.apply_mark(job.task_id)
            self.review_done(job.task_id, job.proc.returncode, out or "", err or "")
        elif job.kind == "final":
            self.final_done(job.proc.returncode, out or "", err or "")

    def apply_mark(self, task_id: str) -> None:
        mark = self.pending_marks.pop(task_id, None)
        st = tasks.load_state(self.p, task_id) if mark else None
        if st:
            st.update(decide=mark[0], decide_round=mark[1])
            tasks.save_state(self.p, st)

    def review_done(self, task_id: str, code: int, out: str, err: str) -> None:
        if code == 0:
            try:
                result = json.loads(out)
            except ValueError:
                result = None
            if isinstance(result, dict) and result.get("verdict"):
                self.on_review(task_id, result)
                return
        if code == 3:  # 이미 도는 리뷰가 있다
            return
        if code == 2 and LOCK_WAIT_TEXT in err:
            self.retry_reviews.add(task_id)
            return
        if code == 2 and any(text in err for text in TEST_CONFIG_TEXTS):
            self.decide("test_config", task_id, err.strip()[-300:], files=["docs/planning/tasks.toml"],
                        commands=['orch task new planner "[test] 고치기"'])
            return
        self.decide("escalation", task_id, f"리뷰 실패(exit {code}): {err.strip()[-300:]}", files=self.task_files(task_id))

    def on_review(self, task_id: str, result: dict) -> None:
        st = tasks.load_state(self.p, task_id) or {}
        self.log(f"{task_id} r{result.get('round')} {result.get('verdict')}")
        if result.get("verdict") == "APPROVE":
            if st.get("pending_dep_fix"):
                self.dep_fix(task_id)
            else:
                self.guard(lambda: self.accepted(task_id, tasks.accept(self.p, task_id)), task_id)
        else:
            self.request_changes(task_id, result)
        self.start_ready()

    @staticmethod
    def repeated_test_failure(st: dict) -> bool:
        reviews = [h for h in st.get("history") or [] if h.get("stage") == "review"][-2:]
        return len(reviews) == 2 and all(
            h.get("source") == "orch" and "quick" in (h.get("tests") or {}) and h["tests"]["quick"] not in testgate.PASSED
            for h in reviews)

    def fix_with_pending(self, task_id: str, note: str | None, retry: bool = False, **kw) -> dict:
        """tasks.fix. 선행 변경 note(pending_dep_fix)가 남아 있으면 앞에 붙여 함께 전하고, 성공한 뒤에만 지운다.
        거부는 OrchError 로 올린다(대기열에 들어간 fix 도 note 를 담고 있어 지운다). --retry 는 이미 전한 지시를 다시 띄우므로 건드리지 않는다."""
        pending = None if retry else (tasks.load_state(self.p, task_id) or {}).get("pending_dep_fix")
        full = "\n".join(x for x in (pending, note) if x) or None
        result = tasks.fix(self.p, self.cfg, task_id, full, retry, **kw)
        if pending:
            st = tasks.load_state(self.p, task_id) or {}
            if st.pop("pending_dep_fix", None) is not None:
                tasks.save_state(self.p, st)
        return result

    def run_fix(self, task_id: str, note: str | None) -> bool:
        """관제가 정한 절차의 fix. 거부되면 못 전한 지시와 함께 메인에 올린다."""
        pending = (tasks.load_state(self.p, task_id) or {}).get("pending_dep_fix")
        done = []
        self.guard(lambda: done.append(self.fix_with_pending(task_id, note)), task_id, pending)
        return bool(done)

    def request_changes(self, task_id: str, result: dict) -> None:
        st = tasks.load_state(self.p, task_id) or {}
        review = result.get("review") or f"docs/reviews/{task_id}/r{st.get('round')}.md"
        stop = [f'orch drop {task_id} --reason "<이유>"']
        if self.repeated_test_failure(st):
            self.decide("repeat_test_failure", task_id, "같은 quick 테스트 실패로 orch 사전 검사가 두 라운드 연속 반려했다",
                        files=[review], commands=[f'orch fix {task_id} --note "<지시>"', *stop])
            return
        if int(st.get("round") or 1) >= self.cfg.max_rounds:
            summary = f"최대 {self.cfg.max_rounds} 라운드에도 승인되지 않았다"
            if st.get("pending_dep_fix"):
                summary += f". 아직 전하지 못한 선행 변경: {st['pending_dep_fix']}"
            self.decide("max_rounds", task_id, summary[:600], files=[review],
                        commands=[f"orch fix {task_id} --extra-round", *stop])
            return
        owned = [f for f in result.get("untracked") or [] if matches_any(f, st.get("owns") or [])]
        note = ("리뷰 중 소유 경로에 추적 안 된 파일이 생겼다. 커밋하거나 지운다: " + ", ".join(owned)) if owned else None
        self.run_fix(task_id, note)

    def dep_fix(self, task_id: str) -> None:
        """디자인 승인 답에서 선행 디자인이 바뀐 태스크가 승인됐다. accept 한 뒤 선행 변경 note 로 고친다.
        note 는 고치기가 성공할 때까지 상태에 남는다."""
        if self.guard(lambda: tasks.accept(self.p, task_id), task_id) is None:
            return
        self.run_fix(task_id, None)

    # --- accept 뒤
    def accepted(self, task_id: str, result: dict) -> dict:
        st = tasks.load_state(self.p, task_id) or {}
        role, status = st.get("role"), result.get("status")
        if status == "done" and role == "planner":
            self.apply_plan(task_id)
        elif status == "done" and role == testgate.TESTER_ROLE:
            self.batch_done(task_id)
        elif status == "awaiting_user":
            self.design_step()
        if result.get("released") is False:
            comm.post(self.p, "progress", f"{task_id} 워커 해제 실패: {json.dumps(result.get('release_error'), ensure_ascii=False)[:200]}")
        return result

    def apply_plan(self, task_id: str) -> None:
        doc = tasks.load_doc(self.p, self.cfg, task_id)
        append = doc.front.get("plan") == "append"
        try:
            out = plan.apply(self.p, self.cfg, append)
        except OrchError as exc:
            if exc.code == 12:
                raise
            if "새 key 가 없다" in str(exc):
                comm.post(self.p, "progress", f"기획 적용: {task_id} 가 새 태스크를 더하지 않았다")
                return
            self.create_task("planner", "tasks.toml 고치기", "docs/planning/tasks.toml",
                             f"orch plan apply{' --append' if append else ''} 가 거부했다. 오류 원문:\n{exc}\n\n"
                             "기존 `docs/planning/` 을 읽고 이 오류만 고친다.",
                             "`orch plan check` 가 통과한다", front={"plan": "append"} if append else None)
            return
        lines = [f"{t['id']} [{t['role']}] {t['title']}" + (f" ← {', '.join(t['depends_on'])}" if t["depends_on"] else "")
                 for t in out["tasks"]]
        comm.post(self.p, "progress", "기획 적용: " + "; ".join(lines))

    # --- 묶음 후속
    def already_made(self, source_line: str) -> bool:
        """이 출처 줄로 만든 태스크가 이미 있는가(다시 뜬 관제가 같은 태스크를 또 만들지 않게)."""
        return any(source_line in path.read_text(encoding="utf-8") for path in self.p.tasks_dir.glob("T-*.md"))

    def batch_done(self, task_id: str) -> None:
        """묶음 테스트(tester)가 끝났다: 후속 태스크를 만들고, 고정 에이전트 리셋을 표시하고, 메인에 알린다."""
        source = f"출처: {task_id} followups"
        record = self.finish_state()
        batches = list(record.get("batches") or [])
        first = task_id not in batches
        if first and not self.already_made(source):
            found = followups.followups(self.p, task_id)
            self.make_followups(found, source, task_id)
        if first:
            names = agents.mark_reset_pending(self.p, self.cfg)  # 멱등: reset_pending=True 만 적는다
            self.save_finish(batches=[*batches, task_id])
            comm.post(self.p, "progress",
                      f"묶음 완료: {task_id}" + (f". 리셋할 에이전트: {', '.join(names)}" if names else ""))

    def make_followups(self, found: dict, source: str, origin: str) -> None:
        """회귀는 원인 태스크마다 회귀 수정 태스크, P2·P3 는 정리 태스크 하나, 기획 누락과 원인 모를 회귀는 메인에 올린다."""
        members = set(found.get("tasks") or [])
        for entry in found.get("regressions") or []:
            causes = sorted({t for t in re.findall(r"T-\d{3,}", entry["text"]) if t in members and t != entry["task"]})
            if len(causes) != 1:
                self.decide("unknown_regression", origin, entry["text"][:300],
                            files=[f"docs/reviews/{entry['task']}/r{entry['round']}.md"],
                            commands=['orch task new developer "회귀 수정: <요약>"'],
                            extra={"source": source})
                continue
            owns = (tasks.load_state(self.p, causes[0]) or {}).get("owns") or None
            self.create_task("developer", f"회귀 수정: {entry['text'][:40]}", ", ".join(owns or []) or "원인 태스크의 소유 경로",
                             f"{source}\n{entry['text']}",
                             f"이 항목의 실패 테스트가 `orch test {{id}}` 에서 통과한다: {entry['text']}", owns=owns)
        items = found.get("followups") or []
        if items:
            owns = sorted({o for e in items for o in (tasks.load_state(self.p, e["task"]) or {}).get("owns") or []})
            lines = "\n".join(f"- {e['id'] or '-'}: {e['text']}" for e in items)
            self.create_task("developer", f"묶음 정리: {origin}", ", ".join(owns) or "항목이 가리키는 파일",
                             f"{source}\n{lines}", "항목마다 고쳤거나 고치지 않은 이유를 리포트에 적었다", owns=owns or None)
        gaps = found.get("planning_gaps") or []
        if gaps:
            self.decide("planning_gaps", origin, f"기획 누락 {len(gaps)}건: " + "; ".join(g["text"][:60] for g in gaps),
                        files=sorted({f"docs/reviews/{g['task']}/r{g['round']}.md" for g in gaps}),
                        commands=['orch scope T-xxx "<추가 범위>"', 'orch task new planner "<증분 기획>"'],
                        extra={"items": gaps, "source": source})
        if any(st.get("status") == "pending" for st in tasks.all_states(self.p)):
            self.start_ready()

    # --- 마무리
    def finish_state(self) -> dict:
        data = read_json(self.p.drive_dir / "finish.json", {})
        return data if isinstance(data, dict) else {}

    def save_finish(self, **fields) -> None:
        write_json(self.p.drive_dir / "finish.json", {**self.finish_state(), **fields})

    def open_judgments(self) -> list[dict]:
        """열린 질문에 outbox 줄의 decide 종류를 붙인다."""
        kinds = {e.get("id"): e.get("kind") for e in comm.read_outbox(self.p) if e.get("type") == "decide"}
        return [{**q, "kind": kinds.get(q["id"])} for q in ask.open_questions(self.p)]

    def finish_step(self) -> None:
        """모든 태스크가 done·dropped 면: 묶음 밖 후속(한 번만) → [test] 가 있으면 전체 테스트 → done 보고(태스크 묶음마다 한 번)."""
        states = tasks.all_states(self.p)
        if not states or any(st.get("status") not in tasks.FINISHED for st in states) or "final" in self.jobs:
            return
        if any(q.get("type") == "decide" and q.get("kind") in OPEN_JUDGMENTS for q in self.open_judgments()):
            return  # 메인의 판단(기획 누락·원인 모를 회귀·토큰 갱신)이 열려 있다
        ids = sorted(st["id"] for st in states)
        record = self.finish_state()
        if record.get("reported") == ids or record.get("final_failed") == ids:
            return
        if not record.get("rest_done") and not self.already_made("출처: rest followups"):
            self.save_finish(rest_done=True)
            self.make_followups(followups.followups(self.p, rest=True), "출처: rest followups", "rest")
            if any(st.get("status") == "pending" for st in tasks.all_states(self.p)):
                return
        try:
            has_test = testgate.load_config(self.p) is not None
        except OrchError:
            has_test = True  # 형식 오류는 전체 테스트가 알린다
        if has_test:
            self.jobs["final"] = Job("final", None, self.spawn(["test", "--full"]))
            self.log("마지막 전체 테스트 시작")
            return
        self.report_done(ids)

    def final_done(self, code: int, out: str, err: str) -> None:
        ids = sorted(st["id"] for st in tasks.all_states(self.p))
        try:
            data = json.loads(out) if out.strip() else {}
        except ValueError:
            data = {}
        if code == 0:
            self.report_done(ids, data)
        elif code == 1:
            self.save_finish(final_failed=ids)
            self.decide("unknown_regression", None, "마지막 전체 테스트가 실패했다. 원인 태스크를 정해 회귀 수정 태스크를 만든다",
                        files=[data["log"]] if data.get("log") else [],
                        commands=['orch task new developer "회귀 수정: <요약>"'],
                        extra={"tail": data.get("tail"), "source": "출처: final followups"})
        elif code == 2 and LOCK_WAIT_TEXT in err:
            self.log("전체 테스트 잠금 대기 초과. 다음 확인 때 다시 한다")
        else:
            self.save_finish(final_failed=ids)
            self.decide("test_config", None, f"전체 테스트를 돌리지 못했다(exit {code}): {err.strip()[-300:]}",
                        files=["docs/planning/tasks.toml"], commands=['orch task new planner "[test] 고치기"'])

    def report_done(self, ids: list[str], final: dict | None = None) -> None:
        states = tasks.all_states(self.p)
        rows = [f"- {st['id']} [{st.get('role')}] {st.get('title')}: {st.get('status')}, "
                f"r{st.get('round')} {(st.get('verdicts') or {}).get(str(st.get('round')), '-')}" for st in states]
        bases = [st["base"] for st in states if st.get("base")]
        changed = git(self.p.root, "diff", "--name-only", f"{bases[0]}..HEAD", check=False).split() if bases else []
        body = ["모든 태스크가 끝났다.", *rows]
        if changed:
            body.append("바뀐 주요 파일: " + ", ".join(changed[:15]) + (" …" if len(changed) > 15 else ""))
        if final:
            body.append(f"마지막 전체 테스트: {final.get('status')} ({final.get('seconds')}초)")
        comm.post(self.p, "done", "\n".join(body))
        self.save_finish(reported=ids)

    # --- 디자인 승인
    def design_step(self) -> None:
        """승인 대상 태스크가 모두 리뷰를 지나 awaiting_user 면: 시스템 추가 제안이 있으면 보강부터, 없으면 승인 질문."""
        roles = self.cfg.user_approval_roles
        gated = [st for st in tasks.all_states(self.p) if st.get("role") in roles]
        if not any(st.get("status") == "awaiting_user" for st in gated):
            return
        if plan.approval_busy(self.p, self.cfg):
            return
        if self.supplement_step(gated):
            return
        try:
            out = plan.design_review(self.p, self.cfg)
        except OrchError as exc:
            if exc.code in (2, 3):  # 아직 기다리거나 이미 물었다
                return
            raise
        self.log(f"디자인 승인 질문 {out['id']}: {', '.join(out['tasks'])}")

    def collect_proposals(self, gated: list[dict]) -> dict[str, tuple[int, str]]:
        """awaiting_user 화면 디자이너 리포트의 마지막 `## r{n}` 절에서 `### 시스템 추가 제안`. "없음" 과 이미
        docs/design/direction.md 에 출처(T-xxx rN)가 기록된 것은 뺀다."""
        direction = self.p.root / "docs/design/direction.md"
        recorded = direction.read_text(encoding="utf-8") if direction.is_file() else ""
        found = {}
        for st in gated:
            if st.get("status") != "awaiting_user" or st.get("role") != "designer":
                continue
            report = self.p.root / f"docs/tasks/{st['id']}/report.md"
            text = report.read_text(encoding="utf-8") if report.is_file() else ""
            rounds = list(re.finditer(r"^## r(\d+)\s*$", text, re.M))
            if not rounds:
                continue
            last = rounds[-1]
            section = text[last.end():]
            match = re.search(r"^### 시스템 추가 제안[ \t]*\n(.*?)(?=^##+ |\Z)", section, re.M | re.S)
            proposal = match.group(1).strip() if match else ""
            rnd = int(last.group(1))
            body = [re.sub(r"^[-*]\s+", "", ln.strip()) for ln in proposal.splitlines() if ln.strip()]
            if not proposal or all(followups.is_empty(ln) for ln in body) or f"{st['id']} r{rnd}" in recorded:
                continue
            found[st["id"]] = (rnd, proposal)
        return found

    def supplement_step(self, gated: list[dict]) -> bool:
        """보강 태스크를 만들거나 기다리거나 마무리했으면 True(승인 질문을 아직 하지 않는다)."""
        path = self.p.drive_dir / "supplement.json"
        current = read_json(path, {})
        current = current if isinstance(current, dict) else {}
        handled = dict(current.get("handled") or {})
        if current.get("task"):
            sup = tasks.load_state(self.p, current["task"]) or {}
            if sup.get("status") == "done":
                sources = current.get("from") or {}
                # 화면을 먼저 고친다(죽고 다시 와도 이미 고친 화면은 awaiting_user 가 아니라 건너뛴다). 그 뒤에 상태를 적는다.
                for task_id, rnd in sources.items():
                    st = tasks.load_state(self.p, task_id) or {}
                    if st.get("status") != "awaiting_user" or st.get("round") != rnd:
                        continue
                    note = f"디자인 시스템 보강({current['task']})을 반영한다"
                    pending = st.get("pending_dep_fix")
                    self.guard(lambda t=task_id, n=note: self.fix_with_pending(t, n, supplement=current["task"]), task_id,
                               "\n".join(x for x in (pending, note) if x))
                handled.update(sources)
                write_json(path, {"done": current, "handled": handled})
                self.decide("token_update", current["task"],
                            f"디자인 시스템 보강 {current['task']} 이 끝났다. UI 기반 개발 태스크에 바뀐 토큰을 반영할지 정한다",
                            files=["docs/design/direction.md", f"docs/tasks/{current['task']}/report.md"],
                            commands=['orch task new developer "토큰 갱신 반영: <요약>"', 'orch scope T-xxx "<바뀐 토큰>"'])
                write_json(path, {"done": current, "handled": handled, "token_update": True})
                self.start_ready()
                return True
            if sup.get("status") not in tasks.FINISHED:
                return True  # 보강 진행 중
            handled.update(current.get("from") or {})
            write_json(path, {"dropped": current, "handled": handled})
        elif current.get("done") or current.get("dropped"):
            handled.update((current.get("done") or current.get("dropped") or {}).get("from") or {})
            if current.get("done") and not current.get("token_update"):
                # 상태만 쓰고 멈춘 경우: token_update 를 한 번 올린다
                task = current["done"]["task"]
                self.decide("token_update", task,
                            f"디자인 시스템 보강 {task} 이 끝났다. UI 기반 개발 태스크에 바뀐 토큰을 반영할지 정한다",
                            files=["docs/design/direction.md", f"docs/tasks/{task}/report.md"],
                            commands=['orch task new developer "토큰 갱신 반영: <요약>"', 'orch scope T-xxx "<바뀐 토큰>"'])
                write_json(path, {**current, "handled": handled, "token_update": True})
        proposals = {t: v for t, v in self.collect_proposals(gated).items() if str(handled.get(t)) != str(v[0])}
        if not proposals:
            return False
        lines = "\n".join(f"- {t} r{rnd}: {text}" for t, (rnd, text) in proposals.items())
        new = self.create_task("director", f"디자인 시스템 보강: {', '.join(proposals)} 제안",
                               "docs/design/system/, docs/design/direction.md",
                               "화면 디자이너의 시스템 추가 제안이다. 제안마다 받아들일지 정해 디자인 시스템에 더하고, 결정과 "
                               "이유를 출처(태스크 ID·라운드)와 함께 `docs/design/direction.md` 에 적는다.\n" + lines,
                               "제안마다 반영 여부와 이유가 출처와 함께 `docs/design/direction.md` 에 있다",
                               owns=["docs/design/system/**", "docs/design/direction.md"])
        write_json(path, {"task": new, "from": {t: rnd for t, (rnd, _) in proposals.items()}, "handled": handled})
        self.start_ready()
        return True

    def create_task(self, role: str, title: str, target: str, change: str, acceptance: str,
                    owns: list[str] | None = None, depends_on=(), front: dict | None = None) -> str:
        """관제가 정해진 규칙으로 만드는 태스크(회귀 수정, 묶음 정리, 보강, 기획 수정). spec 을 경로 지정 커밋하고 pending 으로 둔다.
        acceptance 의 `{id}` 는 새 태스크 ID 로 바꾼다."""
        task_id = tasks.new_task(self.p, self.cfg, role, title)["id"]
        lines = [f"id: {task_id}", f"role: {role}", f"title: {title}"]
        if owns:
            lines.append("owns: " + ", ".join(owns))
        if depends_on:
            lines.append("depends_on: " + ", ".join(depends_on))
        lines += [f"{key}: {value}" for key, value in (front or {}).items()]
        path = tasks.task_path(self.p, task_id)
        path.write_text("---\n" + "\n".join(lines) + "\n---\n"
                        f"## Target\n{target}\n\n## Change\n{change}\n\n"
                        "## Constraints\n다른 태스크의 소유 경로와 이 태스크 범위 밖은 건드리지 않는다\n\n"
                        f"## Acceptance\n{acceptance.replace('{id}', task_id)}\n", encoding="utf-8")
        doc = tasks.load_doc(self.p, self.cfg, task_id)
        git_commit_paths(self.p.root, [str(path.relative_to(self.p.root))], f"[{task_id}] spec: {title}")
        tasks.save_state(self.p, {"id": task_id, "role": role, "title": title, "status": "pending", "stage": "work",
                                  "round": 1, "base": None, "owns": tasks.owns_of(doc, self.cfg),
                                  "depends_on": list(depends_on), "task_id": None, "dispatch_id": None,
                                  "history": [], "verdicts": {}})
        self.log(f"{task_id} 생성 [{role}] {title}")
        return task_id

    def start_ready(self) -> None:
        try:
            out = plan.start_ready(self.p, self.cfg)
        except OrchError as exc:
            if exc.code == 12:
                raise
            self.log(f"start-ready 실패: {exc}")
            return
        for row in [*out.get("fixes", []), *out["started"]]:
            if row.get("status") in ("launch_failed", "error"):
                self.decide("escalation", row["id"], f"작업자를 띄우지 못했다: {row.get('error')}"[:300],
                            files=self.task_files(row["id"]),
                            commands=["orch status", f'orch drop {row["id"]} --reason "<이유>"'])

    # --- 재확인
    @staticmethod
    def launch_age(st: dict) -> float:
        for entry in reversed(st.get("history") or []):
            if entry.get("dispatch_id") and entry.get("at"):
                try:
                    return (datetime.now().astimezone() - datetime.fromisoformat(entry["at"])).total_seconds()
                except ValueError:
                    break
        return float("inf")

    def check_worker(self, st: dict) -> None:
        task_id = st["id"]
        try:
            busy, desc, info = tasks.worker_check(st)
        except OrchError as exc:
            if exc.code == 12:
                raise
            self.log(f"{task_id} worker_check 실패: {exc}")
            return
        info = info or {}
        agent = st.get("agent") if st.get("agent") in self.cfg.agents else None
        if info.get("state") == "start_unknown":
            if self.launch_age(st) < UNKNOWN_GRACE_SECONDS:
                return
            out = self.guard(lambda: tasks.recover_unknown_start(self.p, self.cfg, task_id), task_id) or {}
            self.log(f"{task_id} start_unknown: {out.get('status')}")
            if out.get("status") == "escalate":
                summary = f"작업자 시작을 {out.get('count')}번 확인하지 못했다"
                if out.get("release_error"):
                    summary += f". 이전 작업자 터미널 정리 실패: {json.dumps(out['release_error'], ensure_ascii=False)}"
                self.decide("escalation", task_id, summary[:300], files=self.task_files(task_id),
                            commands=[f'orch fix {task_id} --force --note "<지시>"', f'orch drop {task_id} --reason "<이유>"'])
            return
        if busy is not False:
            return
        if info.get("stage") == "settled" and info.get("state") == "succeeded":
            if agent:
                agents.release_task(self.p, agent, task_id)
            self.start_review(task_id)
            return
        if agent:
            agents.detach(self.p, agent, task_id)
        if int(st.get("round") or 1) >= self.cfg.max_rounds:
            self.decide("max_rounds", task_id, f"작업자가 worker_done 없이 끝났고 최대 라운드다({desc})",
                        files=self.task_files(task_id),
                        commands=[f"orch fix {task_id} --extra-round", f'orch drop {task_id} --reason "<이유>"'])
            return
        self.guard(lambda: tasks.fix(self.p, self.cfg, task_id, tasks.DIED_NOTE.format(task_id=task_id)), task_id)

    def settle_resets(self) -> None:
        """끝나지 않은 인수인계(resetting)를 정리한다: 디스패치가 끝났거나 워커가 없거나 너무 오래됐을 때."""
        for name, e in agents.load_all(self.p).items():
            resetting = e.get("resetting") if isinstance(e, dict) else None
            if isinstance(resetting, dict):
                self.isolated(f"agent:{name}", f"{name} 인수인계 정리", lambda: self.settle_reset(name, resetting))

    def settle_reset(self, name: str, resetting: dict) -> None:
        info = orca.worker_info(resetting["dispatch_id"]) if resetting.get("dispatch_id") else None
        info = info or {}
        try:
            age = (datetime.now().astimezone() - datetime.fromisoformat(resetting["at"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            age = 0
        if not (info.get("stage") == "settled" or info.get("verdict") in ("exited", "dead") or age > RESET_STUCK_SECONDS):
            return
        ok = info.get("state") == "succeeded"
        out = agents.finish_reset(self.p, name, ok=ok)
        self.log(f"{name} 리셋 정리: {out['status']}")
        if not ok:
            self.decide("escalation", None, f"{name} 에이전트 인수인계가 끝나지 않았다", commands=[f"orch agent reset {name}"])

    def reconcile_task(self, st: dict) -> None:
        task_id, status = st["id"], st.get("status")
        open_decide = (st.get("decide") and st["decide"] not in comm.load_answered(self.p)["ids"]
                       and st.get("decide_round") == st.get("round"))
        if f"review:{task_id}" in self.jobs or open_decide:
            return  # 리뷰 중이거나 이 라운드는 메인 판단을 기다린다(답이 오면 다시 본다)
        if status == "reviewing" and not pid_alive(tasks.review_pid(self.p, task_id)):
            self.start_review(task_id)
        elif status == "approved":
            self.on_review(task_id, {"verdict": "APPROVE", "round": st.get("round")})
        elif status == "changes" and not st.get("queued_fix"):
            last = [h for h in st.get("history") or [] if h.get("stage") == "review"][-1:]
            self.on_review(task_id, {"verdict": "REQUEST_CHANGES", "round": st.get("round"),
                                     "untracked": (last[0].get("untracked") if last else None) or []})
        elif status == "working":
            self.check_worker(st)

    def settle_batches(self) -> None:
        """accept 뒤 batch_done 전에 멈춘 묶음 테스트(done tester)의 후속을 한 번 처리한다."""
        done = set(self.finish_state().get("batches") or [])
        for st in tasks.all_states(self.p):
            if st.get("role") == testgate.TESTER_ROLE and st.get("status") == "done" and st["id"] not in done:
                self.beat()
                self.isolated(f"task:{st['id']}", f"{st['id']} 묶음 후속", lambda t=st["id"]: self.batch_done(t), st["id"])

    def reconcile(self) -> None:
        """메일 없이 끝난 일을 찾는다: 끊긴 리뷰, 잠금으로 미룬 리뷰, accept 전에 멈춘 승인, 죽은 워커, 끝나지 않은 인수인계."""
        self.last_reconcile = self.clock()
        self.isolated("reconcile:setup", "재확인 준비", lambda: agents.forget_dead(self.p, self.cfg))
        self.isolated("reconcile:reset", "인수인계 정리", self.settle_resets)
        for task_id in sorted(self.retry_reviews):
            self.retry_reviews.discard(task_id)
            self.isolated(f"task:{task_id}", f"{task_id} 리뷰 다시 시작", lambda: self.start_review(task_id), task_id)
        for st in self.isolated("reconcile:list", "태스크 목록 읽기", lambda: list(tasks.all_states(self.p))) or []:
            self.beat()
            self.isolated(f"task:{st['id']}", f"{st['id']} 재확인", lambda: self.reconcile_task(st), st["id"])
        self.isolated("reconcile:batches", "묶음 후속 재확인", self.settle_batches)
        for name, step in (("start_ready", self.start_ready), ("design_step", self.design_step),
                           ("finish_step", self.finish_step)):
            self.isolated(f"reconcile:{name}", name, step)

    def resets(self) -> None:
        for name in self.cfg.agents:
            out = agents.begin_reset(self.p, self.cfg, name)
            if not out:
                continue
            self.log(f"{name} 리셋 시작: {out['status']}")
            if out["status"] == "failed":
                self.decide("escalation", None, f"{name} 에이전트에 인수인계 태스크를 넣지 못했다",
                            commands=[f"orch agent reset {name}"])
                agents.update(self.p, name, lambda e: e.update(reset_pending=False))

    def tick(self, wait_ms: int = WAIT_MS) -> None:
        self.beat()
        for msg in self.check(wait_ms):
            self.isolated(f"msg:{msg.get('id')}", f"메시지 {msg.get('id')} 처리", lambda msg=msg: self.handle_message(msg),
                          self.msg_task(msg), describe=lambda exc, msg=msg: self.message_failure(msg, exc))
        if self.isolated("queue", "메인 요청 처리", self.run_queue):
            self.isolated("start_ready", "start_ready", self.start_ready)
        self.isolated("poll_jobs", "자식 프로세스 확인", self.poll_jobs)
        self.beat()
        self.isolated("resets", "에이전트 리셋", self.resets)
        if self.last_reconcile is None or self.clock() - self.last_reconcile >= RECONCILE_SECONDS:
            self.isolated("reconcile", "재확인", self.reconcile)

    def run_forever(self) -> None:
        while True:
            self.tick()
            time.sleep(IDLE_SECONDS * 5 if self.check_failed else IDLE_SECONDS)  # Orca 가 내려갔을 때 로그를 덜 쌓는다


def main(p: Project, cfg: Config) -> int:
    """관제 탭 셸에서 `orch drive` 로 띄운다. 하나만 돈다(state/drive/drive.pid)."""
    if os.environ.get("CLAUDECODE"):
        raise OrchError("orch drive 는 관제 탭 셸에서만 돈다. Claude 세션 안에서는 띄우지 않는다(메인은 /orca-team:up)", 2)
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        raise OrchError("ORCA_TERMINAL_HANDLE 이 없다. orch drive 는 Orca 터미널(관제 탭)에서 돈다", 2)
    p.ensure_state()
    p.drive_dir.mkdir(parents=True, exist_ok=True)
    acquire_pid(p.drive_pid, "orch drive")
    try:
        update_run(p, lambda r: (r.update(drive_handle=handle), r.pop("launched_at", None)))
        drive = Drive(p, cfg)
        drive.beat()
        with comm._sigterm_exits():
            drive.log(f"관제 시작({handle}): {drive.bind()['status']}")
            drive.isolated("reconcile", "시작 재확인", drive.reconcile)
            drive.run_forever()
    finally:
        release_pid(p.drive_pid)
    return 0
