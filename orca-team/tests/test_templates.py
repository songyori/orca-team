import json
import re
import tomllib
import unittest

from helpers import PLUGIN
from orch import ask, plan
from orch.config import Config

T = PLUGIN / "templates"
COMMIT_DOCS = sorted([*T.rglob("*.md"), *(PLUGIN / "skills").rglob("SKILL.md")])


class TemplateTest(unittest.TestCase):
    def test_config_enables_auto_up(self):
        text = (T / "config.toml").read_text(encoding="utf-8")
        self.assertIs(tomllib.loads(text)["coordinator"]["auto_up"], True)
        self.assertIn("# auto_up:", text)

    def test_model_table_matches_spec(self):
        cfg = tomllib.loads((T / "config.toml").read_text(encoding="utf-8"))
        table = {name: (r["claude_model"], r["claude_effort"], r["codex_model"], r["codex_effort"])
                 for name, r in cfg["roles"].items()}
        self.assertEqual(table, {
            "planner": ("opus", "xhigh", "gpt-6.1-sol", "high"),
            "director": ("opus", "xhigh", "gpt-6.1-sol", "high"),
            "designer": ("sonnet", "xhigh", "gpt-6.1-sol", "medium"),
            "developer": ("opus", "xhigh", "gpt-6-astra", "xhigh"),
            "tester": ("sonnet", "xhigh", "gpt-6.1-sol", "high"),
        })
        self.assertEqual((cfg["roles"]["director"]["title"], cfg["roles"]["director"]["owns"]),
                         ("디자인 디렉터", ["docs/design/**", "assets/**"]))
        self.assertEqual((cfg["coordinator"]["model"], cfg["coordinator"]["effort"]), ("opus", "xhigh"))
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
                    "timeout: 600000", "--pending", "--wait out-NNNN", "수정안 1개", "새 3안", "### 금지 목록 점검",
                    ".orchestration/design-rules.md", "## 보강 태스크", "AskUserQuestion"):
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
        for key in ("이모지", "Lucide", "Phosphor", "Tabler", "border-left", "border-l-4", "파랑→보라", "글래스모피즘",
                    "블롭", "3열 카드", "가운데 정렬", "Inter", "한글 글꼴", "4.5:1", "3:1", "Unlock the power of",
                    "AI-powered", "lorem ipsum", "### 금지 목록 점검", "orch review"):
            self.assertIn(key, text)
        self.assertEqual(len(re.findall(r"^\d+\. ", text, re.M)), 10)

    def test_text_rules(self):
        text = (T / "text-rules.md").read_text(encoding="utf-8")
        for heading in ("## §0 범위", "## §1 보존", "## §2 맞춤법·표기", "## §3 문체·용어 일관성", "## §4 AI 티·번역투",
                        "## §5 리뷰 심각도", "## 출처"):
            self.assertIn(f"\n{heading}", text)
        for key in ("docs/planning/voice.md", "`### 문구 점검`", "{name}", "%s", "하여야 한다", "할 수 있다",
                    "A가 아니라 B", "되어지다", "에 있어서", "P1(REQUEST_CHANGES)", "P2:", "P3:", "orch review",
                    ".orchestration/design-rules.md", "https://github.com/epoko77-ai/im-not-ai",
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
        for key in ("{role_doc}", "{task_body}", "{owns}", "{fix_block}", "[{task_id}]", "worker_done"):
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

    def test_design_tool_rules(self):
        designer = (T / "roles" / "designer.md").read_text(encoding="utf-8")
        for key in ("Claude Design", "quickstart", '"design"', "/orca-team:image", "docs/design/"):
            self.assertIn(key, designer)
        coordinator = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("Claude Design", coordinator)
        self.assertIn("/orca-team:image", coordinator)
        for name in ("CLAUDE.section.md", "AGENTS.md"):
            self.assertIn("/orca-team:image", (T / name).read_text(encoding="utf-8"), name)

    def test_coordinator_rules(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        for key in ("orch bind", "orch wait", "run_in_background", "orch review", "orch accept", "orch fix",
                    "orch notify --type question", "[main]"):
            self.assertIn(key, text)

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
        coordinator = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertEqual(coordinator.count('-- .orchestration/tasks/T-xxx.md`'), 2)

    def test_review_instructions_run_in_background(self):
        for line in (T / "coordinator.md").read_text(encoding="utf-8").splitlines():
            if "`orch review" in line:
                self.assertIn("run_in_background: true", line, line)

    def test_coordinator_relays_untracked_and_release_errors(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"`untracked`.*orch fix .*--note")
        self.assertRegex(text, r"`released: false`.*`release_error`.*orch notify --type progress")
        self.assertIn("orch drop T-xxx --force", text)

    def test_fix_round_needs_no_manual_release(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("worker-release` 를 손으로 하지 않는다", text)
        self.assertNotIn("--terminal", text)

    def test_main_tells_dash_messages_after_double_dash(self):
        self.assertIn('orch tell -- "<메시지>"', (T / "CLAUDE.section.md").read_text(encoding="utf-8"))

    def test_coordinator_restart_follows_status_next(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("`next`", text)
        self.assertIn("orch drop", text)
        self.assertNotIn("status 를 `working` 으로 고친", text)

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
                    "ask(기획·디자인 디렉터 워커의 질문), approval(디자인 일괄 승인), question(코디네이터 질문. 답은 코디네이터로 간다)",
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

    def test_coordinator_questions_use_structured_json(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("orch notify --type question --json -", text)
        self.assertRegex(text, r'"recommended": \d')
        for key in ('"example"', '"reason"', '"options"'):
            self.assertIn(key, text)
        self.assertNotIn("사용자에게 묻는 경우는 셋뿐", text)
        self.assertIn("기획이 끝난 뒤 사용자에게 묻는 것은", text)
        self.assertIn("애매한 디자인 답", text)

    def test_planner_runs_plan_check_before_confirmation(self):
        text = (T / "roles/planner.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"확정 질문을 보내기 전에 `\.orchestration/bin/orch plan check`")
        self.assertRegex(text, r'interview\.md.*"확정".*다시 묻지 않는다')

    def test_coordinator_parallel_rules(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        for key in ("orch task new planner", "orch plan apply", "orch plan apply --append", "orch start-ready",
                    "orch design-review", "awaiting_user", "docs/planning/spec.md", "docs/planning/contracts.md",
                    "docs/planning/interview.md", "max_parallel", "launch_failed", "done 또는 dropped",
                    'orch fix T-xxx --note "<사용자 요청', "기획이 끝난 뒤 사용자에게 묻는 것은",
                    'orch fix T-yyy --note "선행 디자인 T-xxx 가 바뀐다',
                    "후속 태스크가 `working` 이면 그 결과를 그대로 accept 하지 않는다"):
            self.assertIn(key, text)
        self.assertNotIn("이 태스크를 기다리던 태스크가 있으면 `orch start`", text)

    def test_readme_summarizes_grilling_and_parallel(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("orch ask-user", "orch answer", "orch plan apply", "orch start-ready", "orch design-review",
                    "max_parallel", "user_approval_roles", "awaiting_user", "`pending`"):
            self.assertIn(key, text)

    def test_readme_documents_final_review_changes(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("orch notify --type question --json -", "orch plan check", "orch reask", "open_questions",
                    "`max_parallel` 은 `orch start-ready` 만 지킨다", "선행이 `awaiting_user` 여도 시작해"):
            self.assertIn(key, text)
        self.assertEqual(text.count("| status | next |"), 1)

    def test_spec_section_13_final_review(self):
        text = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = text[text.index("## 13."):]
        for key in ("orch notify --type question --json", "orch plan check", "orch reask", "state/answered.json",
                    "선행 태스크가 dropped 면", "(M4)", "(M5)", "REVIEW_WAIT_STATUSES"):
            self.assertIn(key, section)

    def test_coordinator_wait_loop_and_parallel_cap(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("타임아웃이면 먼저 `orch status` 를 실행해 태스크마다 `next` 를 따른 뒤", text)
        self.assertIn("`max_parallel` 은 `orch start-ready` 만 지키는 상한이다", text)

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
                    "`### 금지 목록 점검`", "빈 상태, 로딩, 오류", "반응형", "가장 가까운 기존 토큰"):
            self.assertIn(key, text)
        self.assertNotIn("docs/design/system.md", text)

    def test_developer_uses_tokens_and_components_only(self):
        text = (T / "roles/developer.md").read_text(encoding="utf-8")
        for key in ("docs/design/system/tokens.json", "CSS 변수", "공통 컴포넌트", "토큰과 공통 컴포넌트만 쓴다",
                    ".orchestration/design-rules.md", "`### 금지 목록 점검`"):
            self.assertIn(key, text)

    def test_coordinator_handles_director_and_system_proposals(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        for key in ("## 시스템 추가 제안", 'orch task new director "디자인 시스템 보강: <요약>"',
                    "owns: docs/design/system/**, docs/design/direction.md",
                    'orch fix T-xxx --supplement T-yyy --note "디자인 시스템 보강(T-yyy)을 반영한다"',
                    'orch task new developer "토큰 갱신 반영: <요약>"', "- director:", "디자인 디렉터(director) 워커",
                    "아래 \"시스템 추가 제안\"", "가장 마지막 `## r{round}` 절", "추가 범위", "`orch status` 에 보이지 않는다"):
            self.assertIn(key, text)
        self.assertLess(text.index("## 시스템 추가 제안"), text.index("## 사용자에게 묻기"))

    def test_role_lists_name_director(self):
        self.assertIn("기본 5개(planner·director·designer·developer·tester)",
                      (PLUGIN / "skills/init/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("`docs/design/direction.md`", (T / "CLAUDE.section.md").read_text(encoding="utf-8"))

    def test_readme_documents_design_director(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("## 디자인 디렉터", "planner·director·designer·developer·tester", "orch ask-user", "design_system_url",
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
        coordinator = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("`docs/planning/voice.md`", coordinator)

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
        self.assertIn("자동으로 다시 띄운다(config.toml auto_up)", claude)
        self.assertNotIn("세션을 다시 열었으면 `/orca-team:up` 을 먼저 실행한다", claude)
        up = (PLUGIN / "skills/up/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("수동 복구", up)
        readme = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("auto_up = false", "자동으로 다시 띄운다", "docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md",
                    "10분 동안은 다시 띄우지 않고", "같은 안내는 바뀔 때와 세션 시작 때만 한다"):
            self.assertIn(key, readme)
        spec = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        self.assertIn("### 코디네이터 자동 부활 (2026-11-06, 0.5.1)", spec[spec.index("## 13."):])


if __name__ == "__main__":
    unittest.main()
