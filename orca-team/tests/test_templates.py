import json
import re
import tomllib
import unittest

from helpers import PLUGIN
from orch import ask, plan, tasks, testgate
from orch.config import Config

T = PLUGIN / "templates"
COMMIT_DOCS = sorted([*T.rglob("*.md"), *(PLUGIN / "skills").rglob("SKILL.md")])


class TemplateTest(unittest.TestCase):
    def test_config_enables_auto_up(self):
        text = (T / "config.toml").read_text(encoding="utf-8")
        self.assertIs(tomllib.loads(text)["drive"]["auto_up"], True)
        self.assertIn("# auto_up:", text)
        self.assertNotIn("[coordinator]", text)

    def test_model_table_matches_spec(self):
        cfg = tomllib.loads((T / "config.toml").read_text(encoding="utf-8"))
        table = {name: (r["claude_model"], r["claude_effort"], r["codex_model"], r["codex_effort"])
                 for name, r in cfg["roles"].items()}
        self.assertEqual(table, {
            "planner": ("opus", "xhigh", "gpt-6.1-sol", "xhigh"),
            "director": ("opus", "xhigh", "gpt-6.1-sol", "xhigh"),
            "designer": ("sonnet", "xhigh", "gpt-6.1-sol", "xhigh"),
            "developer": ("opus", "xhigh", "gpt-6.1-sol", "xhigh"),
            "tester": ("sonnet", "xhigh", "gpt-6.1-sol", "xhigh"),
        })
        self.assertEqual({name: (a["title"], a["roles"], a["claude_model"]) for name, a in cfg["agents"].items()}, {
            "production": ("생산", ["planner", "designer", "developer"], "opus"),
            "research": ("조사", ["director"], "opus"),
            "test": ("테스트", ["tester"], "sonnet")})
        self.assertEqual((cfg["overflow"]["enabled"], cfg["defaults"]["codex_effort"]), (True, "xhigh"))
        self.assertEqual((cfg["roles"]["director"]["title"], cfg["roles"]["director"]["owns"]),
                         ("디자인 디렉터", ["docs/design/**", "assets/**"]))
        self.assertEqual(cfg["review"]["max_rounds"], 3)
        for name, role in cfg["roles"].items():
            self.assertTrue(role["owns"], name)
            self.assertTrue((T / "roles" / f"{name}.md").is_file(), name)

    def test_director_role_doc(self):
        text = (T / "roles" / "director.md").read_text(encoding="utf-8")
        for key in ("## 단계 ① 레퍼런스", "## 단계 ② 톤 시안", "## 단계 ③ 디자인 시스템", "## 질문 보내기",
                    "orca tab create --url", "orca screenshot --page", "orca tab close --page", "result.browserPageId",
                    "자기가 연 탭만 닫는다", "${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/refs/", "캡처 불가",
                    "docs/design/references.md", "docs/design/board/", "docs/design/pilots/", "docs/design/system/",
                    "design_system_url: <URL>", "Design System 타입", '"quickstart"', "orch ask-user --task",
                    "timeout: 600000", "--pending", "--wait out-NNNN", "수정안 1개", "새 3안", "### 디자인 점검",
                    ".orchestration/design-rules.md", "## 보강 태스크", "AskUserQuestion", "§3 의 7번",
                    "word-break: keep-all", "눌림"):
            self.assertIn(key, text)

    def test_director_questions_pass_orch_validation(self):
        text = (T / "roles" / "director.md").read_text(encoding="utf-8")
        questions = [ask.validate_question(json.loads(raw)) for raw in re.findall(r"<<'EOF'\n(.*?)\nEOF", text, re.S)]
        self.assertEqual([q["question"] for q in questions],
                         ["어느 스타일 묶음이 이 서비스에 맞나요?", "어느 톤으로 갈까요?",
                          "이 디자인 시스템으로 전체 화면을 만들까요?"])
        for q in questions:
            self.assertIn("https://claude.ai/artifact/", q["context"])
            self.assertNotEqual(q["header"], ask.DEFAULT_HEADER)

    def test_design_rules(self):
        text = (T / "design-rules.md").read_text(encoding="utf-8")
        headings = ("# 디자인 규칙", "## §0 범위", "## §1 금지 목록 (AI 티)", "## §2 접근성", "## §3 색", "## §4 글자",
                    "## §5 레이아웃", "## §6 표면·동작", "## §7 상태", "## 즉시 차단 항목", "## 자동 검사",
                    "## 출처")
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        banned = text[text.index("## §1"):text.index("## §2")]
        self.assertEqual(len(re.findall(r"^\d+\. ", banned, re.M)), 10)
        for key in ("이모지", "Lucide", "Phosphor", "Tabler", "border-left", "border-l-4", "파랑→보라", "글래스모피즘",
                    "블롭", "3열 카드", "가운데 정렬", "Inter", "한글 글꼴", "4.5:1", "3:1", "Unlock the power of",
                    "AI-powered", "lorem ipsum", "### 디자인 점검", "orch review", ":focus-visible",
                    "prefers-reduced-motion", "user-scalable=no", "word-break: keep-all", "tabular-nums", "0.96",
                    "@media (hover: hover)", "overscroll-behavior: contain", "Storybook", "커밋하지 않는다",
                    "44pt", "48dp", "https://github.com/jakubkrehel/skills", "MIT"):
            self.assertIn(key, text)
        self.assertNotIn("### 금지 목록 점검", text)
        self.assertNotRegex(text, r"\d~\d")

    def test_text_rules(self):
        text = (T / "text-rules.md").read_text(encoding="utf-8")
        for heading in ("## §0 범위", "## §1 보존", "## §2 맞춤법·표기", "## §3 문체·용어 일관성", "## §4 AI 티·번역투",
                        "## §5 리뷰 심각도", "## 출처"):
            self.assertIn(f"\n{heading}", text)
        for key in ("docs/planning/voice.md", "`### 문구 점검`", "{name}", "%s", "하여야 한다", "할 수 있다",
                    "A가 아니라 B", "되어지다", "에 있어서", "P1(REQUEST_CHANGES)", "P2:", "P3:", "orch review",
                    ".orchestration/design-rules.md", "design-rules.md` §1 의 1번", "https://github.com/epoko77-ai/im-not-ai",
                    "https://github.com/amondnet/yoonmoon", "MIT"):
            self.assertIn(key, text)
        self.assertIn("designer·developer 태스크", text)
        self.assertLess(text.index("## §1 보존"), text.index("## §4 AI 티·번역투"))
        self.assertNotRegex(text, r"\d~\d")

    def test_parallel_and_approval_defaults(self):
        cfg = tomllib.loads((T / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual((cfg["max_parallel"], cfg["user_approval_roles"]), (4, ["designer"]))

    def test_append_sections_start_with_marker(self):
        for name in ("CLAUDE.section.md", "AGENTS.md"):
            self.assertTrue((T / name).read_text(encoding="utf-8").startswith("<!-- orca-team -->\n"), name)

    def test_placeholders(self):
        task = (T / "task.md").read_text(encoding="utf-8")
        for key in ("{id}", "{role}", "{title}", "## Target", "## Change", "## Constraints", "## Acceptance"):
            self.assertIn(key, task)
        spec = (T / "worker-spec.md").read_text(encoding="utf-8")
        for key in ("{role_doc}", "{task_body}", "{owns}", "{fix_block}", "{test_block}", "[{task_id}]", "worker_done"):
            self.assertIn(key, spec)
        prompt = (T / "review-prompt.md").read_text(encoding="utf-8")
        for key in ("REVIEW_FILE: {review_path}", "<execution_policy>", "{commits}", "{files}", "VERDICT: APPROVE",
                    "VERDICT: REQUEST_CHANGES"):
            self.assertIn(key, prompt)
        self.assertIn("{design_section}", prompt)
        design = (T / "review-design.md").read_text(encoding="utf-8")
        for key in ("{design_lint}", ".orchestration/design-rules.md", "docs/design/system/tokens.json",
                    "### 디자인 점검", "### 금지 목록 점검", "### 시스템 추가 제안", "REQUEST_CHANGES", "`(새로 생김)`",
                    "`(회귀)`", "`(기존)`", "즉시 차단 항목", "prefers-reduced-motion", "320px", "지우기",
                    "확인 못 함", "15개", "box-shadow"):
            self.assertIn(key, design)
        self.assertEqual(design.count("{design_lint}"), 1)
        self.assertEqual(re.findall(r"^(\d+)\. ", design, re.M), [str(n) for n in range(5, 14)])
        self.assertNotRegex(design, r"\d~\d")
        self.assertIn("{design_section}{text_section}## REVIEW_FILE 형식", prompt)
        text = (T / "review-text.md").read_text(encoding="utf-8")
        for key in ("## 문구 기준", "{text_lint}", ".orchestration/text-rules.md", "docs/planning/voice.md",
                    "### 문구 점검", "text-rules §5", "P3", "orch 문구 자동 검사", ".orchestration/roles/"):
            self.assertIn(key, text)

    def test_blocker_list_matches_between_rules_and_review(self):
        rules = (T / "design-rules.md").read_text(encoding="utf-8")
        review = (T / "review-design.md").read_text(encoding="utf-8")
        section = rules[rules.index("## 즉시 차단 항목"):rules.index("## 자동 검사")]
        in_rules = set(re.findall(r"^- (.+)$", section, re.M))
        p1 = review[review.index("   - P1:"):review.index("   - P2:")]
        in_review = set(re.findall(r"^     - (.+)$", p1, re.M))
        self.assertEqual(len(in_review), 10)
        self.assertEqual(in_rules, in_review)
        state = rules[rules.index("## §7 상태"):rules.index("## 즉시 차단 항목")]
        for key in ("되돌리기", "다시 시도", "임시 페이지를 연결했다면 확인 뒤 되돌린다"):
            self.assertIn(key, state)
        self.assertIn("예외가 인정되지 않는다", rules)
        self.assertIn("즉시 차단 항목은 리포트에 예외를 적었어도 P1 이다.", review)
        self.assertIn("이 태스크가 만든 것(`(새로 생김)`·`(회귀)`)은 REQUEST_CHANGES 사유다", review)
        self.assertIn("`(기존)`(§1 위반 포함)", review)
        self.assertIn("대비는 토큰·색 값으로 계산할 수 있으면", review)
        self.assertIn("§ 제목이 없는 이전 버전 규칙 파일이면 P2 는 적지 않고", review)
        self.assertIn("developer 는 ask 로 묻는다.", rules[rules.index("## §3"):rules.index("## §4")])
        self.assertIn("동작 줄이기 설정에서는 줄이지 않는다(§2 의 10번)", rules)
        self.assertIn("임시 페이지를 연결했다면 확인 뒤 되돌린다",
                      (T / "roles" / "developer.md").read_text(encoding="utf-8"))

    def test_design_tool_rules(self):
        designer = (T / "roles" / "designer.md").read_text(encoding="utf-8")
        for key in ("Claude Design", "quickstart", '"design"', "/orca-team:image", "docs/design/"):
            self.assertIn(key, designer)
        for name in ("CLAUDE.section.md", "AGENTS.md"):
            self.assertIn("/orca-team:image", (T / name).read_text(encoding="utf-8"), name)

    def test_commit_instructions_use_pathspec(self):
        """공유 인덱스에서 남의 스테이징까지 커밋하지 않도록 모든 커밋 지시는 `git commit ... -- <경로>` 형식이다."""
        spans = []
        for doc in COMMIT_DOCS:
            for span in re.findall(r"`([^`]*\bgit commit\s+\S[^`]*)`", doc.read_text(encoding="utf-8")):
                spans.append(span)
                if "--allow-empty" in span:  # 빈 저장소의 첫 커밋은 경로가 없다
                    continue
                self.assertRegex(span, r"\bgit commit\b.* -- \S", f"{doc.relative_to(PLUGIN)}: {span}")
        self.assertGreaterEqual(len(spans), 4)
        worker = (T / "worker-spec.md").read_text(encoding="utf-8")
        self.assertIn("docs/tasks/{task_id}/report.md && git commit -m", worker)
        main = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(main.count('-- .orchestration/tasks/T-xxx.md`'), 1)

    def test_main_rules_cover_decide_and_queue(self):
        text = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        for kind in ("worker_failed", "worker_question", "escalation", "max_rounds", "repeat_test_failure",
                     "test_config", "planning_gaps", "unknown_regression", "token_update"):
            self.assertIn(f"| {kind} |", text)
        for key in ("orch design-decide", "orch scope", "--extra-round", "plan: append", "관제가 꺼져 있다", "result",
                    "반드시 `orch answer <id> \"<처리 내용>\"` 로 닫아야"):
            self.assertIn(key, text)
        self.assertNotIn("orch tell", text)
        self.assertNotIn("coordinator", text)

    def test_main_started_tasks_rule_and_no_drive_only_commands(self):
        text = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        self.assertIn("`orch task new` 만으로는 시작되지 않는다", text)
        self.assertIn('"waiting"', text)
        rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| ") and "|" in line[2:]}
        for kind in ("test_config", "planning_gaps", "unknown_regression", "token_update"):
            self.assertRegex(rows[kind], "시작|orch start", kind)
        for doc in (T / "CLAUDE.section.md", T.parent / "skills/up/SKILL.md", T.parent / "skills/init/SKILL.md"):
            body = doc.read_text(encoding="utf-8")
            for bad in ("orch review ", "orch design-review", "orch plan apply", "orch bind", "orca orchestration worker"):
                self.assertNotIn(bad, body, f"{doc.name}: {bad}")

    def test_planner_task_rows_say_plan_append(self):
        text = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| ") and "|" in line[2:]}
        for kind in ("test_config", "planning_gaps"):
            self.assertIn("plan: append", rows[kind], kind)

    def test_readme_recovery_table_matches_next_action(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        part = text.split("## 복구")[1].split("- `orch drop")[0]
        rows = {m.group(1): m.group(2) for m in re.finditer(r"^\s*\| `(\w+)` \| (.*) \|$", part, re.M)}
        self.assertIn("design-decide", rows["awaiting_user"])
        for status in ("awaiting_user", "approved", "reviewing"):
            for bad in ("orch review", "orch design-review", "orch accept"):
                self.assertNotIn(bad, rows[status], f"{status}: {bad}")
        self.assertIn("관제가 리뷰 지적대로 수정 라운드를 띄운다", rows["changes"])
        self.assertIn("--extra-round", rows["changes"])
        self.assertNotIn("관제(`orch drive`)는 이것을 따른다", part)

    def test_readme_upgrade_steps(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        part = text.split("### 0.6.0 이하 프로젝트를 올리기")[1].split("## ")[0]
        self.assertIn("orch upgrade --force", part)
        self.assertLess(part.index("/clear"), part.index("/orca-team:up"))
        self.assertNotIn("orch bind", text)
        self.assertNotIn("메인에는 progress 로 알린다", text)
        self.assertNotIn("question·approval", text)

    def test_init_reads_claude_section_before_first_request(self):
        text = (PLUGIN / "skills/init/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<!-- orca-team -->", text)

    def test_no_coordinator_left_in_templates_and_code(self):
        self.assertFalse((T / "coordinator.md").exists())
        roots = [T, T.parent / "skills", T.parent / "lib" / "orch", T.parent / "hooks"]
        legacy = {"config.py", "setup.py"}  # 옛 [coordinator] 설정을 옮기라는 안내와 upgrade 거부 문구만 남긴다
        hits = [str(f) for root in roots for f in root.rglob("*") if f.is_file() and f.suffix in (".md", ".py")
                and f.name not in legacy and "코디네이터" in f.read_text(encoding="utf-8")]
        self.assertEqual(hits, [])

    def test_code_does_not_route_main_to_question_notify(self):
        # orch answer 는 question 에 답하지 않는다. 메인에게 notify --type question 을 시키면 막다른 길이다
        hits = [str(f) for f in (T.parent / "lib" / "orch").rglob("*.py")
                if "notify --type question 으로 메인에" in f.read_text(encoding="utf-8")]
        self.assertEqual(hits, [])

    def test_planner_interview_rules(self):
        text = (T / "roles" / "planner.md").read_text(encoding="utf-8")
        for key in ("orch ask-user --task", "--json -", "timeout: 600000", "--wait out-NNNN", "--pending", "exit 4",
                    "exit 3", "AskUserQuestion", "한 번에 한 질문", "예시, 추천, 추천 사유", "자동 결정", "확정 (추천)",
                    "수정할 부분 있음", "docs/planning/spec.md", "docs/planning/contracts.md",
                    "docs/planning/tasks.toml", "docs/planning/interview.md", "depends_on", "질문 수에는 상한이 없다",
                    "`context`", "4000자 이하", "docs/planning/voice.md", "산출물 다섯 개"):
            self.assertIn(key, text)
        for item in ("목적과 성공 기준", "사용자와 사용 맥락", "기능 범위", "사용자 흐름", "화면 목록", "데이터와 상태",
                     "예외와 오류 처리", "비기능(성능, 접근성, 보안, 지원 환경)", "기술 스택과 실행 환경",
                     "인터페이스 약속(API, 데이터 형태, 모듈 경계)", "브랜드 자산(로고, 색, 글꼴)과 사용자가 아는 레퍼런스. 없으면 없다고 적는다",
                     "문구 기준(서비스 문구·정책 문서·사용자 문서의 문체, 사용자 호칭, 버튼·메뉴 라벨 형태, 핵심 용어 표기)", "범위 밖"):
            self.assertIn(f"- {item}\n", text)

    def test_question_docs_say_render_adds_example_prefix(self):
        for name in ("planner", "director"):
            with self.subTest(role=name):
                text = (T / "roles" / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn("앞에 '예:' 를 붙이지 않는다(화면이 붙인다)", text)
        self.assertIn('`header`(12자 이하. 없으면 "디자인")', (T / "roles" / "director.md").read_text(encoding="utf-8"))

    def test_planner_examples_pass_orch_validation(self):
        text = (T / "roles" / "planner.md").read_text(encoding="utf-8")
        questions = re.findall(r"<<'EOF'\n(.*?)\nEOF", text, re.S)
        self.assertEqual(len(questions), 2)  # 일반 질문, 확정 질문
        checked = [ask.validate_question(json.loads(raw)) for raw in questions]
        confirm = checked[1]  # 확정 질문: 요약은 context(기획 핵심, 자동 결정, 태스크 그래프 표), example 은 짧게
        for text_part in ("기획 핵심", "자동 결정", "| key | 직무 | 선행 |"):
            self.assertIn(text_part, confirm["context"])
        self.assertLess(len(confirm["example"]), 100)
        block = re.search(r"```toml\n(.*?)```", text, re.S).group(1)
        cfg = Config(tomllib.loads((T / "config.toml").read_text(encoding="utf-8")))
        tasks = plan.parse_plan(tomllib.loads(block), cfg)
        self.assertEqual(plan.graph_problems(tasks), [])
        self.assertTrue(any(t.depends_on for t in tasks))
        self.assertEqual(plan.director_problems(tasks, cfg), [])
        self.assertEqual([t.role for t in tasks].count("director"), 1)
        self.assertIn("| design-direction | director | - |", confirm["context"])
        data = tomllib.loads(block)
        self.assertEqual(testgate.config_problems(data["test"]), [])
        self.assertEqual(plan.group_problems(tasks), [])
        self.assertIn("| todo-test | tester | ui-foundation, todo-store, todo-ui |", confirm["context"])

    def test_batch_verify_rules_in_templates(self):
        planner = (T / "roles/planner.md").read_text(encoding="utf-8")
        for key in ('`["tests/<key>/**", "docs/qa/<key>.md"]`', "{files}", "ok_exit = [0, 5]", "exclusive = true",
                    "`[test]` 는 커밋해야 적용된다",
                    "3–6개", "developer 태스크는 tester 태스크에 의존하지 않는다"):
            self.assertIn(key, planner)
        tester = (T / "roles/tester.md").read_text(encoding="utf-8")
        for key in ("orch test T-xxx --full", "제품 회귀", "`tests/<key>/`", "`docs/qa/<key>.md`",
                    "테스트를 약하게 만들거나 건너뛰거나 지우지 않는다", "자동 반려하지 않는다"):
            self.assertIn(key, tester)
        developer = (T / "roles/developer.md").read_text(encoding="utf-8")
        self.assertIn("워커 spec 의 `## 테스트` 절", developer)

    def test_planner_graph_rules_for_director(self):
        text = (T / "roles/planner.md").read_text(encoding="utf-8")
        for key in ('["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**", '
                    '"docs/design/pilots/**", "docs/design/system/**"]',
                    "시안 화면 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면)", "`docs/design/screens/<화면>/**`",
                    "UI 기반", "director 태스크에 의존한다", "director 가 사용자에게 직접 묻는다", "orch plan check",
                    "에셋만 만드는 designer 태스크(owns 가 모두 `assets/` 아래)는 director 에 의존하지 않아도 된다"):
            self.assertIn(key, text)
        self.assertNotIn("디자인 방향(톤, 참고 자료)", text)

    def test_main_display_rules(self):
        text = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        for key in ("`.orchestration/bin/orch answer <id>", "`render.question`", "`render.header`", "`render.options`",
                    "label·description 을 순서 그대로", "단일 선택(`multiSelect: false`)", "Other 로 자유 입력",
                    "질문을 바꾸거나 합치지 않고", "하나씩 따로 묻는다",
                    "ask(기획·디자인 디렉터 워커의 질문)",
                    "`render` 가 없는 legacy question"):
            self.assertIn(key, text)
        self.assertRegex(text, r"`render\.context` 가 있으면 .*먼저.*평문.*AskUserQuestion")
        item = next(line for line in text.splitlines() if line.lstrip().startswith("- ask(기획·디자인 디렉터 워커의 질문)"))
        self.assertRegex(item, r"\(1\) `render\.context`[^()]*그대로 출력.*\(2\) .*AskUserQuestion.*\(3\) .*orch answer")
        self.assertRegex(text, r"`render` 가 있으면\(ask, approval, question\) 그대로 AskUserQuestion")
        self.assertNotIn("tell --reply", text)

    def test_worker_spec_names_ask_roles(self):
        text = (T / "worker-spec.md").read_text(encoding="utf-8")
        self.assertIn("기획(planner)·디자인 디렉터(director) 워커만 직무 정의대로 `orch ask-user` 로 사용자에게 묻는다", text)

    def test_planner_runs_plan_check_before_confirmation(self):
        text = (T / "roles/planner.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"확정 질문을 보내기 전에 `\.orchestration/bin/orch plan check`")
        self.assertRegex(text, r'interview\.md.*"확정".*다시 묻지 않는다')

    def test_readme_summarizes_grilling_and_parallel(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("orch ask-user", "orch answer", "orch plan apply", "orch start-ready", "orch design-review",
                    "max_parallel", "user_approval_roles", "awaiting_user", "`pending`"):
            self.assertIn(key, text)

    def test_readme_documents_final_review_changes(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("orch plan check", "orch reask", "open_questions",
                    "`max_parallel` 은 `orch start-ready` 만 지킨다", "선행이 `awaiting_user` 여도 시작해"):
            self.assertIn(key, text)
        self.assertEqual(text.count("| status | next |"), 1)

    def test_spec_section_13_final_review(self):
        text = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = text[text.index("## 13."):]
        for key in ("orch notify --type question --json", "orch plan check", "orch reask", "state/answered.json",
                    "선행 태스크가 dropped 면", "(M4)", "(M5)", "REVIEW_WAIT_STATUSES"):
            self.assertIn(key, section)

    def test_planner_records_answer_first(self):
        text = (T / "roles/planner.md").read_text(encoding="utf-8")
        self.assertIn("다른 일보다 먼저 그 답을 `docs/planning/interview.md` 에 적는다", text)

    def test_readme_upgrade_from_0_2_0(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("0.2.0 으로 만든 프로젝트는 업그레이드해도 예전 흐름", 'user_approval_roles = ["designer"]` 를 넣는다',
                    "orca-team/templates/CLAUDE.section.md", "`roles/planner.md`", "/orca-team:up"):
            self.assertIn(key, text)

    def test_designer_uses_project_design_system(self):
        text = (T / "roles/designer.md").read_text(encoding="utf-8")
        for key in ("docs/design/direction.md", "design_system_url", "project/README.md", "design_systems: false",
                    "`### 시스템 추가 제안`", "assets/<화면>/", "docs/design/screens/", ".orchestration/design-rules.md",
                    "`### 디자인 점검`", "빈 상태, 로딩, 오류", "반응형", "가장 가까운 기존 토큰", "§7 의 상태"):
            self.assertIn(key, text)
        self.assertNotIn("docs/design/system.md", text)

    def test_developer_uses_tokens_and_components_only(self):
        text = (T / "roles/developer.md").read_text(encoding="utf-8")
        for key in ("docs/design/system/tokens.json", "CSS 변수", "공통 컴포넌트", "토큰과 공통 컴포넌트만 쓴다",
                    ".orchestration/design-rules.md", "`### 디자인 점검`", "Storybook", "커밋하지 않는다"):
            self.assertIn(key, text)

    def test_role_lists_name_director(self):
        self.assertIn("기본 5개(planner·director·designer·developer·tester)",
                      (PLUGIN / "skills/init/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("`docs/design/direction.md`", (T / "CLAUDE.section.md").read_text(encoding="utf-8"))

    def test_readme_documents_design_director(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("## 디자인 디렉터", "고정 에이전트 3개(생산·조사·테스트)", "orch ask-user", "design_system_url",
                    ".orchestration/design-rules.md", "시스템 추가 제안", "`[roles.director]`",
                    "0.3.0 으로 만든 프로젝트는", "orca-team/templates/roles/director.md",
                    "orca-team/templates/design-rules.md", "orch plan check",
                    "docs/superpowers/specs/2026-10-31-orca-team-design-director-design.md",
                    "docs/superpowers/plans/2026-10-31-orca-team-design-director.md",
                    "owns 가 모두 `assets/` 아래인 에셋 전용 designer"):
            self.assertIn(key, text)

    def test_planner_writes_voice_doc(self):
        text = (T / "roles" / "planner.md").read_text(encoding="utf-8")
        for key in ("# 문구 기준", "## 용어집", "| 쓰는 표기 | 쓰지 않는 표기 | 메모 |", "서비스 문구: 해요체",
                    "정책 문서: 하십시오체", "사용자 문서: 해요체", "용어집 표", "`docs/policy/**`", "`docs/user/**`",
                    "`README.md`", "특정 자리에서만 피하는 표기(예: 제목·링크에서만, 이용약관 안에서만)는 메모 칸에 적는다"):
            self.assertIn(key, text)

    def test_writers_follow_text_rules(self):
        for name in ("designer", "developer"):
            with self.subTest(role=name):
                text = (T / "roles" / f"{name}.md").read_text(encoding="utf-8")
                for key in ("## 문구 규칙", ".orchestration/text-rules.md", "docs/planning/voice.md",
                            "`### 문구 점검`", "voice.md 는 고치지 않는다"):
                    self.assertIn(key, text)

    def test_developer_owns_user_and_policy_docs(self):
        cfg = tomllib.loads((T / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["roles"]["developer"]["owns"], ["src/**", "README.md", "docs/user/**", "docs/policy/**"])

    def test_locations_name_text_rules(self):
        claude = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        for key in ("`.orchestration/text-rules.md`", "voice.md"):
            self.assertIn(key, claude)

    def test_spec_section_13_design_director(self):
        text = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = text[text.index("## 13."):]
        for key in ("### 디자인 디렉터 (2026-10-31, 0.4.0)", "ASK_ROLES", "director_problems", "design_lint",
                    "review-design.md", "--append", "`orch fix --supplement T-yyy`", "에셋 전용 designer", "oklch()"):
            self.assertIn(key, section)

    def test_readme_documents_text_rules(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("## 문구 규칙", ".orchestration/text-rules.md", "docs/planning/voice.md", "### 문구 점검",
                    "https://github.com/epoko77-ai/im-not-ai", "https://github.com/amondnet/yoonmoon",
                    "0.4.x 로 만든 프로젝트는", "orca-team/templates/text-rules.md",
                    "docs/superpowers/specs/2026-11-05-orca-team-text-rules-design.md",
                    "docs/superpowers/plans/2026-11-05-orca-team-text-rules.md"):
            self.assertIn(key, text)
        self.assertLess(text.index("## 디자인 디렉터"), text.index("## 문구 규칙"))
        self.assertLess(text.index("## 문구 규칙"), text.index("## 보안 주의"))

    def test_spec_section_13_text_rules(self):
        text = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = text[text.index("## 13."):]
        for key in ("### 문구 규칙 (2026-11-05, 0.5.0)", "text_lint", "{text_section}", "NOTES.md", "파일 복사"):
            self.assertIn(key, section)

    def test_docs_describe_auto_up(self):
        claude = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        self.assertIn("자동으로 다시 띄운다(config.toml `[drive] auto_up`)", claude)
        self.assertNotIn("세션을 다시 열었으면 `/orca-team:up` 을 먼저 실행한다", claude)
        up = (PLUGIN / "skills/up/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("수동 복구", up)
        readme = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("auto_up = false", "자동으로 다시 띄운다", "docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md",
                    "10분 동안은 다시 띄우지 않고", "같은 안내는 바뀔 때와 세션 시작 때만 한다"):
            self.assertIn(key, readme)
        spec = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        self.assertIn("### 코디네이터 자동 부활 (2026-11-06, 0.5.1)", spec[spec.index("## 13."):])

    def test_role_docs_drop_old_report_name(self):
        for name in ("director", "designer", "developer"):
            with self.subTest(role=name):
                text = (T / "roles" / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn("### 디자인 점검", text)
                self.assertNotIn("### 금지 목록 점검", text)
                self.assertIn("§2–§7", text)

    def test_docs_describe_design_rules(self):
        readme = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("### 디자인 점검", "https://github.com/jakubkrehel/skills", "`(새로 생김)`", "`(회귀)`", "즉시 차단 항목",
                    "0.5.1 이하로 만든 프로젝트", "docs/superpowers/specs/2026-11-06-orca-team-design-rules-design.md",
                    "docs/superpowers/plans/2026-11-06-orca-team-design-rules.md"):
            self.assertIn(key, readme)
        self.assertLess(readme.index("## 디자인 디렉터"), readme.index("https://github.com/jakubkrehel/skills"))
        self.assertLess(readme.index("https://github.com/jakubkrehel/skills"), readme.index("## 문구 규칙"))
        spec = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = spec[spec.index("## 13."):]
        for key in ("### 디자인 규칙 (2026-11-06, 0.5.2)", "design_diff_lint", "diff_lines", "lint.cap", "guarded"):
            self.assertIn(key, section)
        status = (PLUGIN.parent / "docs/superpowers/2026-11-05-orca-team-status.md").read_text(encoding="utf-8")
        for key in ("| 0.5.2 |", "specs/2026-11-06-orca-team-design-rules-design.md"):
            self.assertIn(key, status)

    def test_status_doc_describes_batch_verify(self):
        status = (PLUGIN.parent / "docs/superpowers/2026-11-05-orca-team-status.md").read_text(encoding="utf-8")
        for key in ("| 0.6.0 |", "specs/2026-11-08-orca-team-batch-verify-design.md"):
            self.assertIn(key, status)

    def test_status_doc_describes_hotfix(self):
        status = (PLUGIN.parent / "docs/superpowers/2026-11-05-orca-team-status.md").read_text(encoding="utf-8")
        for key in ("| 0.6.1 |", "worker-show 를 --full 로"):
            self.assertIn(key, status)

    def test_status_doc_describes_persistent_agents(self):
        status = (PLUGIN.parent / "docs/superpowers/2026-11-05-orca-team-status.md").read_text(encoding="utf-8")
        for key in ("(2026-11-09, 0.7.0)", "| 0.7.0 |", "0.7.0 전체(설계 §15)"):
            self.assertIn(key, status)

    def test_readme_describes_persistent_agents(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("## 고정 에이전트 (0.7.0)", "orch review-view", "orch agent reset", "0.6.0 이하 프로젝트를 올리기"):
            self.assertIn(key, text)
        self.assertNotIn("orch notify --type question", text)

    def test_handoff_template(self):
        text = (T / "handoff.md").read_text(encoding="utf-8")
        for key in ("{agent}", "{agent_title}", "{path}", "200줄", "worker_done"):
            self.assertIn(key, text)
        self.assertIn("{handoff_block}## 소유 경로", (T / "worker-spec.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
