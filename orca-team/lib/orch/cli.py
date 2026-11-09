"""orch 명령줄. 하위 모듈은 실행 시점에 import 한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .paths import VERSION, OrchError, Project, find_root, git_toplevel


def emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orch", description="orca-team 오케스트레이션 CLI")
    parser.add_argument("--version", action="version", version=f"orca-team {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="설치 점검")
    s = sub.add_parser("scaffold", help="프로젝트에 orca-team 파일 생성")
    s.add_argument("--description-file", required=True)
    sub.add_parser("setup-done", help="세팅 모드 해제")
    sub.add_parser("upgrade", help="복사된 규칙 파일을 이 플러그인 버전 템플릿으로 바꾼다(config.toml 제외, 커밋 안 함)")
    s = sub.add_parser("up", help="코디네이터 확인·기동(메인)")
    s.add_argument("--wait-run", type=int, default=0, metavar="SECONDS")
    sub.add_parser("bind", help="Run 생성·재연결(코디네이터)")
    sub.add_parser("status", help="상태")
    s = sub.add_parser("tell", help="메인 → 코디네이터")
    s.add_argument("body")
    s.add_argument("--reply", metavar="OUT_ID")
    s = sub.add_parser("watch", help="코디네이터 → 메인 메시지 대기(메인, 백그라운드)")
    s.add_argument("--timeout", type=float, metavar="SECONDS")
    s = sub.add_parser("notify", help="코디네이터 → 메인")
    s.add_argument("--type", required=True, dest="kind")
    s.add_argument("body", nargs="?")
    s.add_argument("--json", metavar="FILE", help="구조화 질문 JSON(--type question 만). - 면 표준 입력")
    s = sub.add_parser("wait", help="코디네이터 메일함 대기(코디네이터, 백그라운드)")
    s.add_argument("--ack")
    s.add_argument("--timeout-ms", type=int, default=3600000)
    s = sub.add_parser("ask-user", help="기획·디자인 디렉터 워커 → 메인 직통 질문과 답 대기(그 워커)")
    s.add_argument("--task", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", metavar="FILE", help="질문 JSON 파일. - 면 표준 입력")
    g.add_argument("--wait", metavar="OUT_ID", help="이미 올린 질문의 답을 이어서 기다린다")
    g.add_argument("--pending", action="store_true", help="답은 왔지만 아직 읽지 않은 질문")
    s = sub.add_parser("answer", help="ask·question·approval 에 답한다(메인)")
    s.add_argument("id")
    s.add_argument("body")
    sub.add_parser("reask", help="이미 전했지만 답하지 않은 질문을 watch 형식으로 다시 출력한다(메인)")
    s = sub.add_parser("task", help="태스크 spec")
    task_sub = s.add_subparsers(dest="task_cmd", required=True)
    n = task_sub.add_parser("new")
    n.add_argument("role")
    n.add_argument("title")
    for name in ("start", "fix"):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--retry", action="store_true")
        s.add_argument("--model")
        s.add_argument("--effort")
        if name == "fix":
            s.add_argument("--note")
            s.add_argument("--force", action="store_true", help="워커가 일하는 중이어도 fix 한다")
            s.add_argument("--supplement", metavar="T-yyy",
                           help="반영하는 디자인 시스템 보강 태스크(done 인 director). 이력에 사용자 요청 대신 남는다")
            s.add_argument("--extra-round", action="store_true",
                           help="최대 라운드를 넘긴 태스크를 한 번 더 고친다(사용자가 정했을 때만)")
    for name in ("review", "accept"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("drop", help="태스크 중단·잠금 해제(코디네이터)")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--force", action="store_true", help="워커가 살아 있어도 중단한다")
    s = sub.add_parser("test", help="[test] 명령으로 태스크 관련 테스트(quick)나 전체 테스트(--full)를 돌린다")
    s.add_argument("id", nargs="?", help="태스크 ID. 없으면 --full 로 전체 테스트만 돌린다(마지막 확인)")
    s.add_argument("--full", action="store_true", help="전체 테스트(full)")
    s = sub.add_parser("followups", help="묶음(또는 묶음 밖) 리뷰의 회귀·후속·기획 누락을 모은다(코디네이터, 읽기 전용)")
    s.add_argument("id", nargs="?", help="tester 태스크 ID(그 묶음) 또는 태스크 ID")
    s.add_argument("--rest", action="store_true", help="어떤 묶음에도 들지 않은 done developer 태스크")
    s = sub.add_parser("agent", help="고정 에이전트")
    agent_sub = s.add_subparsers(dest="agent_cmd", required=True)
    agent_sub.add_parser("reset", help="다음 태스크 전에 인수인계를 쓰고 세션을 비운다").add_argument("name")
    s = sub.add_parser("scope", help="일하는 중인 태스크에 추가 범위를 알린다(메인, spec 커밋 뒤)")
    s.add_argument("id")
    s.add_argument("body")
    sub.add_parser("which").add_argument("ref")
    add_graph_commands(sub)
    return parser


def add_graph_commands(sub) -> None:
    """태스크 그래프 명령(코디네이터)."""
    s = sub.add_parser("design-decide", help="디자인 승인 답을 화면별 결정으로 적용한다(메인)")
    s.add_argument("id", help="approval 메시지 id(out-NNNN)")
    s.add_argument("--approve", default="", help="승인한 태스크 ID, 쉼표로 구분")
    s.add_argument("--fix", action="append", default=[], metavar="T-xxx=<요청>", help="고칠 태스크와 사용자 요청 원문")
    s = sub.add_parser("plan", help="태스크 그래프(docs/planning/tasks.toml) 적용(코디네이터)")
    plan_sub = s.add_subparsers(dest="plan_cmd", required=True)
    a = plan_sub.add_parser("apply", help="검증 후 태스크 spec·pending 상태 생성")
    a.add_argument("--append", action="store_true", help="이미 적용한 계획에 새 key 만 더한다")
    c = plan_sub.add_parser("check", help="tasks.toml 을 plan apply 와 같은 규칙으로 검사만 한다(쓰기 없음)")
    c.add_argument("--file", default=None, metavar="PATH", help="검사할 파일(기본 docs/planning/tasks.toml)")
    sub.add_parser("start-ready", help="시작할 수 있는 pending 태스크를 max_parallel 까지 띄운다(코디네이터)")
    sub.add_parser("design-review", help="awaiting_user 디자인을 모아 사용자 승인 질문 하나를 올린다(코디네이터)")


GRAPH_COMMANDS = {"plan", "start-ready", "design-review", "design-decide"}  # 태스크 그래프 명령(코디네이터). add_graph_commands·run_graph 가 처리한다
CONFIG_COMMANDS = {"up", "task", "start", "fix", "review", "agent", *GRAPH_COMMANDS}


def project(need_config: bool):
    """프로젝트가 아니면 exit 2. config.toml 은 필요한 명령만 읽는다(깨져도 소통·조회·정리 명령은 돈다)."""
    p = Project(find_root())
    if not need_config:
        return p, None
    from .config import Config

    return p, Config.load(p.config_path)


def soft_config(p):
    """(설정, 오류 문구). status 는 설정이 깨져도 복구 안내를 보여 준다."""
    from .config import Config

    try:
        cfg = Config.load(p.config_path)
        # 늦게 평가되는 값도 여기서 검증해 status 본문이 죽지 않게 한다
        cfg.max_rounds
        cfg.max_parallel
        cfg.user_approval_roles
        return cfg, None
    except OrchError as exc:
        return None, str(exc)


def overrides(args) -> dict:
    return {k: v for k, v in (("claude_model", args.model), ("claude_effort", args.effort)) if v}


def run_graph(p, cfg, args) -> int:
    from . import plan

    if args.cmd == "design-decide":
        fixes = {}
        for pair in args.fix:
            task_id, sep, note = pair.partition("=")
            if not sep:
                raise OrchError(f"--fix 는 T-xxx=<요청> 형식이다: {pair}", 2)
            fixes[task_id.strip()] = note.strip()
        approve = [t.strip() for t in args.approve.split(",") if t.strip()]
        emit(plan.design_decide(p, cfg, args.id, approve, fixes))
        return 0
    if args.cmd == "plan":
        emit(plan.check_only(p, cfg, args.file) if args.plan_cmd == "check" else plan.apply(p, cfg, args.append))
        return 0
    if args.cmd == "design-review":
        emit(plan.design_review(p, cfg))
        return 0
    out = plan.start_ready(p, cfg)
    emit(out)
    return 1 if any(s["status"] == "launch_failed" for s in out["started"]) else 0


def run(args) -> int:
    if args.cmd == "doctor":
        from . import setup

        checks = setup.doctor(Path.cwd())
        for c in checks:
            mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
            print(f"{mark} {c.name}: {c.detail}")
        return 0 if all(c.ok or not c.required for c in checks) else 1
    if args.cmd == "scaffold":
        from . import setup

        root = (git_toplevel(Path.cwd()) or Path.cwd()).resolve()
        emit(setup.scaffold(root, Path(args.description_file).read_text(encoding="utf-8")))
        return 0
    p, cfg = project(args.cmd in CONFIG_COMMANDS)
    if args.cmd in GRAPH_COMMANDS:
        return run_graph(p, cfg, args)
    if args.cmd in ("setup-done", "up", "bind", "status", "upgrade"):
        from . import setup

        handlers = {"setup-done": lambda: setup.setup_done(p), "up": lambda: setup.up(p, cfg, args.wait_run),
                    "bind": lambda: setup.bind(p), "status": lambda: setup.status(p, *soft_config(p)),
                    "upgrade": lambda: setup.upgrade(p)}
        emit(handlers[args.cmd]())
    elif args.cmd in ("tell", "watch", "notify", "wait"):
        from . import comm
        from .util import require_coordinator

        if args.cmd == "tell":
            emit(comm.tell(p, args.body, args.reply))
        elif args.cmd == "watch":
            def deliver(lines):
                emit(lines)
                sys.stdout.flush()

            if not comm.watch(p, timeout=args.timeout, deliver=deliver):
                emit([])
        elif args.cmd == "notify":
            if args.json is not None:
                from . import ask

                if args.kind != "question" or args.body is not None:
                    raise OrchError("--json 은 --type question 에만 쓰고 본문 인자는 함께 쓰지 않는다", 2)
                emit(ask.notify_question(p, args.json))
            elif args.body is None:
                raise OrchError("본문 또는 --json 이 필요하다", 2)
            else:
                emit(comm.notify(p, args.kind, args.body))
        else:
            require_coordinator(p)
            emit(comm.wait(p, args.ack, args.timeout_ms))
    elif args.cmd in ("ask-user", "answer", "reask"):
        from . import ask

        if args.cmd == "answer":
            emit(ask.answer(p, args.id, args.body))
        elif args.cmd == "reask":
            emit(ask.reask(p))
        else:
            out = ask.ask_user(p, args.task, source=args.json, wait_id=args.wait, pending=args.pending)
            emit(out)
            if out.get("status") == "waiting":
                return ask.WAITING_EXIT
    elif args.cmd == "test":
        from . import testgate

        if args.id is None and not args.full:
            raise OrchError("태스크 ID 또는 --full 이 필요하다", 2)
        out = testgate.run_task(p, args.id, "full" if args.full else "quick") if args.id else testgate.run_final(p)
        emit(out)
        return testgate.exit_code(out)
    elif args.cmd == "followups":
        from . import followups

        if bool(args.id) == bool(args.rest):
            raise OrchError("태스크 ID 와 --rest 중 하나만 준다", 2)
        emit(followups.followups(p, args.id, args.rest))
    elif args.cmd == "review":
        from . import review

        emit(review.review(p, cfg, args.id))
    elif args.cmd == "agent":
        from . import agents

        emit(agents.request_reset(p, cfg, args.name))
    else:
        from . import tasks

        if args.cmd == "task":
            emit(tasks.new_task(p, cfg, args.role, args.title))
        elif args.cmd == "start":
            emit(tasks.start(p, cfg, args.id, args.retry, overrides(args)))
        elif args.cmd == "fix":
            emit(tasks.fix(p, cfg, args.id, args.note, args.retry, overrides(args), args.force, args.supplement,
                           args.extra_round))
        elif args.cmd == "accept":
            emit(tasks.accept(p, args.id))
        elif args.cmd == "drop":
            emit(tasks.drop(p, args.id, args.reason, args.force))
        elif args.cmd == "scope":
            emit(tasks.scope(p, args.id, args.body))
        else:
            emit(tasks.which(p, args.ref))
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except OrchError as exc:
        print(f"orch: {exc}", file=sys.stderr)
        return exc.code
