# orca-team 디자인 디렉터 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 새 직무 `director`(디자인 디렉터)가 기획 뒤 맨 먼저 레퍼런스 보드 → 톤 시안 9장 → Claude Design 디자인 시스템을 사용자에게 `orch ask-user` 로 확인받고, 화면 디자인과 UI 코드는 그 시스템만 쓰며, AI 티 패턴은 금지 목록·Codex 리뷰·orch 자동 검사로 막는다.

**Architecture:** `orch ask-user` 허용 직무를 `ASK_ROLES = ("planner", "director")` 로 넓히고 훅이 director 워커를 `worker:director` 로 표시한다. `orch plan check` 는 config 에 director 가 있으면 designer 태스크가 director 태스크 하나에 의존하는지 검사한다. 새 모듈 `lib/orch/lint.py` 가 바뀐 파일의 이모지·왼쪽 줄 강조·파랑·보라 그라디언트를 찾아 리뷰 프롬프트(`templates/review-design.md`)에 경고로 넣는다. 나머지는 템플릿(director·designer·developer·planner·coordinator·design-rules)이 지시한다.

**Tech Stack:** Python 3.11+ 표준 라이브러리(`re`, `colorsys`, `tomllib`, `unittest`), Orca CLI(내장 브라우저 `orca tab`·`orca screenshot`), Claude Design(Artifact 도구 Design·Design System 타입), 내장 Codex 래퍼.

**설계 문서:** `docs/superpowers/specs/2026-10-31-orca-team-design-director-design.md`(요구사항). 선행 설계 `2026-10-30-orca-team-design.md`, `2026-10-31-orca-team-grilling-parallel-design.md`.

## Global Constraints

- Python 3.11 이상, 표준 라이브러리만 쓴다. f-string 안에 바깥과 같은 따옴표를 겹쳐 쓰지 않는다(3.11 문법).
- 모든 Orca 호출은 `lib/orch/orca.py` 의 `call()` 을 거친다. 이번 계획의 orch 코드는 새 Orca 호출을 더하지 않는다.
- `orch ask-user` 의 JSON 스키마, render, 540초 대기와 exit 4, `--wait`, `--pending` 동작은 바꾸지 않는다. 허용 직무만 넓힌다.
- `user_approval_roles` 는 `["designer"]` 그대로다. director 는 승인 대상이 아니다(Codex APPROVE 뒤 accept 하면 done).
- `[roles.director]` 값: `title = "디자인 디렉터"`, `claude_model = "opus"`, `claude_effort = "xhigh"`, `codex_model = "gpt-6.1-sol"`, `codex_effort = "high"`, `owns = ["docs/design/**", "assets/**"]`.
- 기획이 만드는 director 태스크 owns: `["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**", "docs/design/pilots/**", "docs/design/system/**"]`. 화면 designer 태스크 owns: `docs/design/screens/<화면>/**`.
- 디자인 시스템 URL 은 `docs/design/direction.md` 의 `design_system_url: <URL>` 한 줄이다.
- 레퍼런스 캡처는 저장소 밖 `${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/refs/<프로젝트 폴더 이름>/` 에 둔다. 커밋하지 않는다. director 는 자기가 연 탭만 닫는다.
- 자동 검사 대상 확장자 `.html .css .scss .js .jsx .ts .tsx .vue .svelte .md .json`. 제외 `docs/design/references.md`, `docs/planning/**`, `docs/tasks/**`, `docs/reviews/**`. 1MB(1 << 20 바이트) 넘는 파일은 건너뛴다. 결과는 경고뿐이고 없으면 "없음".
- 자동 검사와 디자인 기준 절은 직무 `director`, `designer`, `developer` 리뷰에만 넣는다.
- 시안은 화면 3개 × 톤 3가지로 고정한다. 피드백이 세부 수정이면 수정안 1개, 방향 전환이면 새 3안.
- 버전 0.4.0: `orca-team/lib/orch/paths.py` 의 `VERSION`, `orca-team/.claude-plugin/plugin.json`, 저장소 `.claude-plugin/marketplace.json`.
- 문서·주석·사용자 메시지는 한국어로 쓴다. 식별자·명령·경로는 원문 그대로 둔다.
- 커밋은 경로를 지정해서만 한다(`git add -- <경로> && git commit -m "..." -- <경로>`). 메시지는 한국어이고 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 를 붙인다. `.superpowers/` 는 커밋하지 않는다.
- `~/.claude/plugins`, 사용자 Orca 탭·터미널은 건드리지 않는다.
- 테스트(저장소 루트에서): 전체 `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`(지금 408개, 경고·잡출력 없이 통과해야 한다), 일부 `cd orca-team/tests && python3 -m unittest <모듈>[.<클래스>[.<메서드>]] -v`. 테스트는 가짜 orca(`tests/fakes/orca`)와 가짜 Codex 래퍼만 쓴다.

## 설계 대비 결정

설계가 열어 둔 부분은 기존 코드와 가장 잘 맞는 쪽으로 정했다. Task 6 에서 기반 설계 §13 에 요약해 남긴다.

1. **plan check 의 `--append`.** 이미 적용한 태스크는 바꿀 수 없으므로(`적용한 key 를 바꿨다` 거부) director 의존 검사는 새로 더하는 designer 태스크만 본다. director 개수(정확히 1개)는 tasks.toml 전체로 센다. 첫 apply 에서는 모든 태스크가 새 태스크라 설계 문구와 같다. 0.3.0 에서 적용한 designer 가 있는 프로젝트도 director 를 더한 뒤 막히지 않는다.
2. **훅의 ask 직무 이름.** 역할 표시 훅은 매 프롬프트 첫 줄에 돌아 가볍게 둔다. `orch.ask` 를 불러오지 않고 정규식 `ASK_WORKER_RE` 에 `planner|director` 를 적는다. 테스트가 `orch.ask.ASK_ROLES` 와 같은지 묶는다.
3. **리뷰 프롬프트 자리.** `templates/review-prompt.md` 에는 `{design_section}` 자리 하나만 둔다. 디자인 기준 단락과 `{design_lint}` 자리는 새 템플릿 `templates/review-design.md` 에 둔다. `compose_prompt` 가 디자인 직무면 그것을 채워 넣고, 다른 직무면 빈 문자열을 넣는다.
4. **자동 검사 세부.** 설계의 세 검사에 더해 JSX 인라인 스타일(`borderLeft: '4px solid …'`)과 rem·em 너비(×16px)도 본다. Tailwind 그라디언트는 `from-`·`via-`·`to-` 어느 쪽이든 파랑 계열(blue·indigo·sky)과 보라 계열(purple·violet·fuchsia)이 한 줄에 함께 있으면 잡는다. CSS 색은 hex·rgb()·hsl()·색 이름을 색상각으로 나눈다(파랑 190~245°, 보라 245~300°, 채도 0.25 미만이나 아주 어둡거나 밝은 색은 뺀다). 경고는 최대 50건 싣고 나머지는 "… 외 N건" 한 줄로 줄인다.
5. **기존 그래프 테스트.** 템플릿 config 에 director 가 들어가면 `tests/test_plan.py` 의 기존 테스트(designer 만 있는 그래프)가 새 검사에 걸린다. 그 클래스들은 `PlanCase`(config 에서 `[roles.director]` 를 지운 0.3.0 설정)로 돌리고, director 규칙은 새 `DirectorRuleTest` 와 `DirectorFlowTest` 가 템플릿 설정 그대로 본다.
6. **director 보강 태스크 소유 경로.** `orch task new` 는 owns 인자가 없다. 코디네이터가 spec front matter 에 `owns: docs/design/system/**, docs/design/direction.md` 줄을 넣는다(기존 `owns_of` 가 front matter 를 우선한다).
7. **레퍼런스 캡처 명령.** 2026-10-31 실제 Orca 로 확인한 출력이다: `orca tab create --url <URL> --json` 은 `result.browserPageId`, `orca screenshot --page <id> --format png --json` 은 `result.data`(base64 PNG), `orca tab close --page <id> --json` 은 `result.closed`. 사용자 탭을 찍거나 닫지 않도록 screenshot 과 close 에 항상 `--page` 를 준다.
8. **메인 지침 문구.** ask 메시지는 이제 기획·디자인 디렉터 워커에서 온다. `templates/CLAUDE.section.md` 의 "ask(기획 워커의 인터뷰 질문)" 을 "ask(기획·디자인 디렉터 워커의 질문)" 으로 바꾼다. 처리 순서와 F1 가드는 그대로다.

## 파일 구조

```
orca-team/
  lib/orch/lint.py              (신규) design_lint: 이모지·왼쪽 줄 강조·파랑·보라 그라디언트 의심 위치
  lib/orch/ask.py               ASK_ROLES, require_ask_role, open_questions 일반화
  lib/orch/plan.py              director_problems, check 에서 호출
  lib/orch/review.py            design_section, compose_prompt 의 {design_section}
  lib/orch/setup.py             SCAFFOLD_FILES 에 roles/director.md, design-rules.md
  lib/orch/cli.py               ask-user 도움말
  lib/orch/paths.py             VERSION 0.4.0
  hooks/orca_team_hook.py       ASK_WORKER_RE, ASK_WORKER_TITLES
  templates/config.toml         [roles.director]
  templates/roles/director.md   (신규) 디자인 디렉터 직무 정의
  templates/design-rules.md     (신규) AI 티 금지 목록
  templates/review-design.md    (신규) 리뷰 프롬프트 디자인 기준 절
  templates/review-prompt.md    {design_section}
  templates/roles/planner.md    점검표, 그래프 규칙, 예시
  templates/roles/designer.md   프로젝트 디자인 시스템 적용
  templates/roles/developer.md  토큰·공통 컴포넌트만
  templates/coordinator.md      director, 시스템 추가 제안
  templates/worker-spec.md      규칙 5
  templates/CLAUDE.section.md   ask 문구, 위치
  skills/init/SKILL.md          기본 직무 5개
  .claude-plugin/plugin.json    0.4.0
  tests/helpers.py              ProjectCase.remove_role
  tests/test_lint.py            (신규)
  tests/test_templates.py, test_setup.py, test_ask.py, test_hooks.py, test_plan.py, test_review.py, test_cli.py
.claude-plugin/marketplace.json 0.4.0
README.md                       디자인 디렉터 절, 0.3.0 업그레이드
docs/superpowers/specs/2026-10-30-orca-team-design.md  §13 한 절
```

---

### Task 1: director 직무 정의, 금지 목록, 설정, scaffold

**Files:**
- Create: `orca-team/templates/roles/director.md`
- Create: `orca-team/templates/design-rules.md`
- Modify: `orca-team/templates/config.toml`(헤더 주석, `[roles.planner]` 뒤에 `[roles.director]`)
- Modify: `orca-team/lib/orch/setup.py:23-31`(`SCAFFOLD_FILES`)
- Test: `orca-team/tests/test_templates.py`, `orca-team/tests/test_setup.py`

**Interfaces:**
- Produces: 템플릿 config 의 `[roles.director]`(Task 3·6 의 테스트가 이 설정으로 돈다), `.orchestration/roles/director.md`, `.orchestration/design-rules.md`(Task 4 리뷰 프롬프트와 Task 5 템플릿이 이 경로를 가리킨다). director.md 의 "질문 보내기" 절 이름(Task 2 훅 거부 문구가 가리킨다).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_templates.py` 의 `test_model_table_matches_spec` 에서 기대 표에 director 를 더하고 owns 를 확인한다.

```python
        self.assertEqual(table, {
            "planner": ("opus", "xhigh", "gpt-6.1-sol", "high"),
            "director": ("opus", "xhigh", "gpt-6.1-sol", "high"),
            "designer": ("sonnet", "xhigh", "gpt-6.1-sol", "medium"),
            "developer": ("opus", "xhigh", "gpt-6-astra", "xhigh"),
            "tester": ("sonnet", "xhigh", "gpt-6.1-sol", "high"),
        })
        self.assertEqual((cfg["roles"]["director"]["title"], cfg["roles"]["director"]["owns"]),
                         ("디자인 디렉터", ["docs/design/**", "assets/**"]))
```

같은 파일 `TemplateTest` 에 테스트 셋을 더한다.

```python
    def test_director_role_doc(self):
        text = (T / "roles" / "director.md").read_text(encoding="utf-8")
        for key in ("## 단계 ① 레퍼런스", "## 단계 ② 톤 시안", "## 단계 ③ 디자인 시스템", "## 질문 보내기",
                    "orca tab create --url", "orca screenshot --page", "orca tab close --page", "result.browserPageId",
                    "자기가 연 탭만 닫는다", "${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/refs/", "캡처 불가",
                    "docs/design/references.md", "docs/design/board/", "docs/design/pilots/", "docs/design/system/",
                    "design_system_url: <URL>", "Design System 타입", '"quickstart"', "orch ask-user --task",
                    "timeout: 600000", "--pending", "--wait out-NNNN", "수정안 1개", "새 3안", "## 금지 목록 점검",
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
                    "AI-powered", "lorem ipsum", "## 금지 목록 점검", "orch review"):
            self.assertIn(key, text)
        self.assertEqual(len(re.findall(r"^\d+\. ", text, re.M)), 10)
```

`orca-team/tests/test_setup.py` 의 `test_creates_files_and_setup_mode` 가 확인하는 경로 목록에 두 파일을 더한다.

```python
        for rel in (".orchestration/config.toml", ".orchestration/coordinator.md", ".orchestration/roles/designer.md",
                    ".orchestration/roles/director.md", ".orchestration/design-rules.md",
                    ".orchestration/bin/orch", ".orchestration/project.md", ".orchestration/tasks/.gitkeep",
                    "CLAUDE.md", "AGENTS.md", ".gitignore"):
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_setup.ScaffoldTest -v 2>&1 | tail -15`
Expected: FAIL. `test_model_table_matches_spec`(director 없음), `test_director_role_doc`·`test_director_questions_pass_orch_validation`·`test_design_rules`(FileNotFoundError), `test_creates_files_and_setup_mode`(director.md 없음).

- [ ] **Step 3: 금지 목록을 만든다**

`orca-team/templates/design-rules.md`:

````markdown
# 디자인 금지 목록 (AI 티 패턴)

디자인 디렉터(director), 화면 디자이너(designer), 개발자(developer)의 UI 작업이 지킨다. Codex 리뷰는 이 목록으로 판정하고, 위반은 REQUEST_CHANGES 사유다. 정당한 예외는 리포트 `## 금지 목록 점검` 절에 근거와 함께 적는다.

1. 이모지를 아이콘, 제목 장식, 글머리, 상태 표시로 쓰지 않는다. 아이콘은 디자인 시스템이 정한 아이콘 세트 하나(Lucide, Phosphor, Tabler 중 하나)만 쓴다.
2. 왼쪽에 굵은 색 줄이 있는 카드·콜아웃(`border-left`·`border-inline-start` 강조, Tailwind `border-l-4` 같은 것)을 쓰지 않는다.
3. 파랑→보라(인디고·바이올렛) 그라디언트와 그라디언트 글자를 쓰지 않는다. 이유 없는 네온 빛, 글래스모피즘, 블롭 배경도 쓰지 않는다.
4. 모든 카드에 같은 큰 둥근 모서리와 부드러운 그림자를 반복하지 않는다. 모서리와 그림자는 시스템 토큰의 위계를 따른다.
5. 정해진 틀 같은 랜딩(큰 그라디언트 제목, 버튼 2개, "아이콘·제목·두 줄 설명" 3열 카드)을 쓰지 않는다.
6. 모든 섹션을 가운데 정렬하고 같은 간격만 반복하지 않는다. 정보 위계에 맞는 정렬과 리듬을 쓴다.
7. 근거 없이 Inter나 시스템 글꼴 기본값을 쓰지 않는다. 한국어 서비스는 한글 글꼴을 정한다.
8. 회색 글자를 남발하지 않는다. 본문 대비는 4.5:1 이상, 24px 이상 글자와 아이콘·테두리는 3:1 이상이다.
9. 뻔한 문구("Unlock the power of", "Seamless", "Elevate", "혁신적인")와 의미 없는 배지("New", "AI-powered")를 쓰지 않는다.
10. 시안과 화면은 그 서비스에 맞는 현실적인 데이터와 문구로 채운다. lorem ipsum이나 "사용자 1" 같은 더미를 쓰지 않는다.

## 자동 검사
`orch review` 는 Codex 를 부르기 전에 태스크가 바꾼 파일에서 1(이모지), 2(왼쪽 줄 강조), 3(파랑·보라 그라디언트)의 의심 위치를 찾아 리뷰 프롬프트에 경고로 넣는다. 블록 인용처럼 정당한 쓰임이 있으므로 자동 불합격은 아니다. 나머지 항목은 리뷰어가 화면과 코드를 직접 보고 판단한다.
````

- [ ] **Step 4: director 직무 정의를 만든다**

`orca-team/templates/roles/director.md`:

````markdown
# director — 디자인 디렉터

## 책임
- 화면 디자인을 시작하기 전에 서비스의 디자인 방향을 정한다. 단계 ① 레퍼런스, ② 톤 시안, ③ 디자인 시스템을 차례로 사용자에게 보여 주고 확인받는다.
- 확정한 디자인 시스템은 화면 디자이너(designer)와 개발자(developer)가 쓰는 유일한 기준이다. 지금 서비스되는 상용 제품 수준을 목표로 하고, AI가 만든 티가 나는 디자인을 만들지 않는다.

## 입력
- `docs/planning/spec.md`(화면 목록, 사용자 흐름), `docs/planning/contracts.md`, `docs/planning/interview.md`(브랜드 자산, 사용자가 아는 레퍼런스)
- 태스크 spec 의 Change: 기획이 정한 시안 화면 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면)
- `.orchestration/design-rules.md`: AI 티 금지 목록. 모든 단계의 산출물이 지킨다.

## 도구
| 일 | 도구 |
|---|---|
| 레퍼런스 조사 | WebSearch, WebFetch |
| 레퍼런스 화면 캡처 | Orca 내장 브라우저(`orca tab create`, `orca screenshot`, `orca tab close`) |
| 레퍼런스 보드, 톤 시안 | Claude Design Design 타입. Artifact 도구를 `action: "quickstart"`, `intent: "design"` 으로 불러 찾은 `type_url` 로 만든다 |
| 디자인 시스템 | Claude Design Design System 타입. Artifact 도구를 `action: "quickstart"`, `intent: "other"` 로 불러 찾는다. 없으면 `action: "list"`, `scope: "types"` 로 다시 찾는다 |
| 래스터 에셋 | `/orca-team:image`(Codex). 결과를 직접 보고 채택한 파일만 `assets/` 에 커밋한다 |

- 타입의 `type_url` 은 계정마다 다를 수 있다. 매번 찾는다.
- Codex 는 claude.ai 아티팩트를 볼 수 없다. 아티팩트를 채운 원본 파일을 아래 "산출물" 경로에 커밋해야 리뷰된다.
- Design System 타입을 끝내 찾지 못하면 멈추고 프리앰블의 ask 명령으로 코디네이터에게 알린다. 코디네이터가 메인(사용자)에게 묻는다.

## 단계 ① 레퍼런스
1. **조사.** 같은 분야의 상용 서비스와 인접 분야에서 완성도가 높은 서비스를 6~9개 고른다. 기준은 서비스 성격, 사용자층, 정보 밀도, 플랫폼(웹·모바일)이다. 국내 서비스면 국내 레퍼런스를 넣는다. `interview.md` 에 사용자가 말한 레퍼런스나 브랜드 자산이 있으면 먼저 반영한다.
2. **캡처.** Orca 내장 브라우저로 각 서비스의 실제 화면을 캡처한다. 캡처 폴더는 저장소 밖 `${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/refs/<프로젝트 폴더 이름>/` 이고, 저장소에 커밋하지 않는다.

   ```bash
   REFS="${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/refs/$(basename "$PWD")"; mkdir -p "$REFS"
   orca tab create --url https://example.com --json    # 출력의 result.browserPageId 가 자기가 연 page id 다
   orca screenshot --page <page id> --format png --json \
     | python3 -c 'import base64,json,sys; open(sys.argv[1],"wb").write(base64.b64decode(json.load(sys.stdin)["result"]["data"]))' "$REFS/<서비스>-<화면>.png"
   orca tab close --page <page id> --json
   ```

   - screenshot 은 탭을 만든 다음 명령에서 실행한다. 저장한 PNG 를 Read 로 열어 보고, 페이지가 덜 떴으면 다시 찍는다.
   - 자기가 연 탭만 닫는다. 사용자나 다른 워커가 연 탭은 찍거나 닫지 않는다. screenshot 과 close 에는 항상 `--page <자기가 연 page id>` 를 준다.
   - 로그인이 필요하거나 막히면 공개된 대표 이미지나 링크만 쓰고 보드에 "캡처 불가"라고 적는다. 멈추지 않는다.
   - Orca 브라우저 명령이 실패하면 링크와 설명만으로 보드를 만들고 리포트에 적는다.
3. **레퍼런스 보드.** Design 타입 캔버스 "레퍼런스 보드"를 만든다.
   - 레퍼런스를 스타일 묶음 3~4개로 나눠 배치한다. 묶음마다 이름(예: "차분한 모노톤·넓은 여백"), 특징 3줄, 이 서비스에 맞는 이유를 쓴다.
   - 레퍼런스마다 서비스 이름, 링크, 가져올 점(색, 글꼴, 레이아웃 밀도, 컴포넌트)을 붙인다.
   - 보드 원본은 `docs/design/board/` 에 둔다.
4. **기록.** `docs/design/references.md` 에 레퍼런스 표(이름, URL, 묶음, 가져올 점, 캡처 여부)와 보드 URL 을 적는다.
5. **질문.** 스타일 묶음을 묻는다(아래 "질문 보내기"). 여러 묶음을 섞거나 사용자가 직접 레퍼런스를 주는 답(Other)은 그대로 따른다.

```bash
.orchestration/bin/orch ask-user --task T-xxx --json - <<'EOF'
{"question": "어느 스타일 묶음이 이 서비스에 맞나요?",
 "example": "차분한 모노톤이면 정보가 많은 화면도 정돈돼 보이고, 선명한 포인트 컬러면 첫인상이 강해진다",
 "context": "레퍼런스 보드: https://claude.ai/artifact/<보드 id>\n\n- 차분한 모노톤·넓은 여백: 회색 위계와 강조색 하나\n- 선명한 포인트 컬러·카드: 브랜드 색을 넓게 쓰고 카드로 묶는다\n- 촘촘한 정보 밀도·표: 작은 글자와 표로 한 화면에 많이 보여 준다",
 "options": [{"label": "차분한 모노톤·넓은 여백", "description": "회색 위계와 강조색 하나. 여백으로 구획한다"},
             {"label": "선명한 포인트 컬러·카드", "description": "브랜드 색을 넓게 쓰고 카드로 묶는다"},
             {"label": "촘촘한 정보 밀도·표", "description": "작은 글자와 표로 한 화면에 많이 보여 준다"}],
 "recommended": 0,
 "reason": "오래 보는 서비스라 눈이 덜 피로하고 정보 위계가 잘 보인다",
 "header": "레퍼런스"}
EOF
```

## 단계 ② 톤 시안
1. 고른 묶음(또는 사용자가 준 레퍼런스) 안에서 서로 분명히 다른 톤&매너 3가지(A·B·C)를 정한다. 색, 글꼴, 밀도, 모서리, 그림자 중 둘 이상이 달라야 한다.
2. Design 타입 캔버스 하나에 톤마다 한 줄씩, 시안 화면 3개를 나란히 놓는다(9장). 그 서비스에 맞는 현실적인 데이터와 문구로 채운다.
3. 시안 원본은 `docs/design/pilots/` 에 둔다.
4. **질문.**

```bash
.orchestration/bin/orch ask-user --task T-xxx --json - <<'EOF'
{"question": "어느 톤으로 갈까요?",
 "example": "A를 고르면 그 색·글꼴·밀도로 디자인 시스템을 만든다. 섞고 싶으면 Other 로 적는다",
 "context": "톤 시안: https://claude.ai/artifact/<시안 id>\n\n- A 잉크: 흰 배경, 먹색 글자, 강조색 하나, 모서리 4px\n- B 종이: 따뜻한 회백색 배경, 세리프 제목, 모서리 0\n- C 선명: 브랜드 초록 면, 굵은 산세리프, 모서리 12px",
 "options": [{"label": "A 잉크", "description": "가장 차분하다. 정보가 많은 화면에 강하다"},
             {"label": "B 종이", "description": "읽는 느낌이 강하다. 글이 많은 화면에 맞는다"},
             {"label": "C 선명", "description": "첫인상이 강하다. 입력 화면이 경쾌하다"}],
 "recommended": 0,
 "reason": "정보가 빽빽한 화면에서 위계가 가장 분명하다",
 "header": "톤 시안"}
EOF
```

5. **답 처리**
   - A·B·C 중 하나면 그 톤으로 확정하고 단계 ③으로 간다.
   - Other(피드백)면 내용을 판단한다.
     - 세부 수정(예: "B인데 색만 A처럼 차분하게"): 수정안 1개(시안 화면 3개)를 만들고 "이 수정안으로 확정할까요?"를 묻는다. 선택지는 "확정"과 "다시 수정"이고, 다시 고칠 내용은 Other 로 받는다.
     - 방향 전환(예: "다 별로다, 더 대담하게"): 새 3안을 만들어 4번 질문을 다시 한다.
   - 확정할 때까지 반복한다. 라운드마다 캔버스를 새 버전으로 갱신하고 `docs/design/direction.md` 에 피드백과 판단(세부 수정인지 방향 전환인지, 그 이유)을 적는다.

## 단계 ③ 디자인 시스템
1. Design System 타입으로 프로젝트 디자인 시스템을 만든다. 확정한 시안에서 값을 뽑아 정리하고, 타입이 돌려주는 지침(대비 4.5:1, 토큰 이름 규칙, `tokens.json` 목록 형태, 파일 순서)을 따른다.
   - 토큰: 색(라이트 테마 필수, 다크는 기획이 요구할 때만), 글꼴 패밀리와 크기 단계, 간격, 모서리, 그림자
   - 컴포넌트: 기획 화면에 필요한 것 전부. 최소한 버튼, 입력, 선택, 카드, 목록 항목, 내비게이션, 모달, 토스트, 빈 상태, 로딩, 오류. 컴포넌트마다 상태(기본, hover, focus, disabled, error)별 미리보기와 사용 규칙
   - README: 브랜드 원칙, 시각 기초, 아이콘 세트(Lucide·Phosphor·Tabler 중 하나), 사용 규칙
2. 타입이 `project/` 아래에 두라는 파일을 저장소 `docs/design/system/` 아래 같은 상대 경로로 쓴다(예: `project/tokens.json` → `docs/design/system/tokens.json`). 게시할 때 `files` 로 `project/<경로>` 를 그 파일에 연결한다. 개발자와 Codex 는 `docs/design/system/tokens.json` 과 `docs/design/system/README.md` 를 읽는다.
3. **질문.**

```bash
.orchestration/bin/orch ask-user --task T-xxx --json - <<'EOF'
{"question": "이 디자인 시스템으로 전체 화면을 만들까요?",
 "example": "확정하면 화면 디자이너와 개발자가 이 시스템의 토큰과 컴포넌트만 쓴다",
 "context": "디자인 시스템: https://claude.ai/artifact/<시스템 id>\n\n- 주색 #1F6F5C, 본문 Pretendard 16px/1.6, 기본 모서리 6px\n- 컴포넌트: 버튼, 입력, 선택, 카드, 목록 항목, 내비게이션, 모달, 토스트, 빈 상태, 로딩, 오류",
 "options": [{"label": "확정", "description": "이 시스템으로 모든 화면 디자인과 UI 구현을 시작한다"},
             {"label": "수정 요청", "description": "고칠 부분을 Other 로 적어 주세요"}],
 "recommended": 0,
 "reason": "확정한 톤 시안의 값을 그대로 옮겼고 대비 기준을 통과했다",
 "header": "디자인 시스템"}
EOF
```

4. 수정 요청이면 반영하고 다시 묻는다. 확정하면 `docs/design/direction.md` 에 `design_system_url: <URL>` 한 줄을 적는다. 화면 디자이너가 이 줄로 시스템을 찾는다. 그다음 리포트를 쓰고 커밋한 뒤 worker_done 을 보낸다.

## 질문 보내기
- 질문은 `orch ask-user` 로만 한다. AskUserQuestion 은 쓰지 않는다(훅이 막는다). 질문은 코디네이터를 거치지 않고 메인을 통해 사용자에게 바로 간다. `T-xxx` 는 이 태스크 ID 다. Bash 는 `timeout: 600000` 으로 실행한다(orch 가 답을 최대 9분 기다린다).
- 형식은 기획 질문과 같다: `question`(한 문장, 300자 이하), `example`(300자 이하), `options` 2~4개(label 30자 이하), `recommended`(추천 인덱스), `reason`(추천 사유), `header`(12자 이하), `context`(마크다운 4000자 이하. 보드·시안·시스템 링크와 요약). 메인 화면에는 추천 선택지가 맨 앞에 "(추천)"으로 보이고, `context` 가 질문 전에 그대로 보인다.
- 한 번에 한 질문만 한다.
- exit 0: `{"id": "out-NNNN", "answer": "<답>"}`. 다른 일보다 먼저 그 답을 `docs/design/direction.md` 에 적는다(질문 번호, 질문, 답). orch 는 돌려준 답을 읽음으로 기록해 다시 주지 않으므로, 적기 전에 워커가 끝나면 그 답을 잃는다.
- exit 4: `{"id": "out-NNNN", "status": "waiting"}`. 9분 안에 답이 없었다. `.orchestration/bin/orch ask-user --task T-xxx --wait out-NNNN` 을 같은 방식으로 다시 실행해 이어서 기다린다. 새 질문을 보내지 않는다.
- exit 3: 답을 기다리는 질문이 이미 있다. 오류 문구의 `--wait out-NNNN` 으로 그 답부터 기다린다.
- exit 2: 질문 형식 오류다. 오류 문구에 적힌 필드를 고쳐 다시 보낸다.

## 산출물
| 경로 | 내용 |
|---|---|
| `docs/design/direction.md` | 질문과 답, 피드백 판단, 확정 근거, 보드·시안·시스템 URL, `design_system_url: <URL>` 줄 |
| `docs/design/references.md` | 레퍼런스 표와 보드 URL |
| `docs/design/board/` | 레퍼런스 보드 원본 |
| `docs/design/pilots/` | 확정 시안 원본 |
| `docs/design/system/` | 디자인 시스템 원본(`tokens.json`, `README.md`, `components/…`) |
| `docs/tasks/T-xxx/report.md` | 리포트: 보드·시안·시스템 URL, 시안 라운드 수, `## 금지 목록 점검` |

- 리포트 `## 금지 목록 점검` 절에는 `.orchestration/design-rules.md` 항목마다 지켰는지와, 예외가 있으면 그 근거를 적는다.

## 수정 라운드에서 이어 가기
- 이전 워커가 질문의 답을 기다리다 끝났을 수 있다. 먼저 `docs/design/direction.md` 를 읽고 `.orchestration/bin/orch ask-user --task T-xxx --pending` 을 실행한다. `pending` 의 답은 direction.md 에 옮겨 적는다. `waiting` 에 id 가 있으면 새 질문 대신 `--wait <그 id>` 로 그 답부터 기다린다.
- direction.md 에 이미 확정한 단계는 다시 묻지 않는다.
- Codex 리뷰의 REQUEST_CHANGES 는 확정한 방향 안에서 고친다(예: 대비 부족, 금지 패턴, 빠진 컴포넌트 상태). 사용자가 확정한 모습이 눈에 띄게 바뀌는 수정일 때만 `ask-user` 로 다시 확인받는다.

## 보강 태스크
코디네이터가 화면 디자이너들의 "시스템 추가 제안"을 모아 `디자인 시스템 보강: <요약>` 태스크를 만들면 단계 ①②는 하지 않는다.
- 제안마다 받아들일지 정하고, 받아들인 것을 디자인 시스템(Design System 아티팩트와 `docs/design/system/`)에 더한다. 결정과 이유를 `docs/design/direction.md` 에 적는다.
- 확정한 방향과 충돌하는 제안만 `ask-user` 로 사용자에게 묻는다. 나머지는 확정 방향 안에서 정한다.
- `design_system_url` 은 바꾸지 않는다. 같은 아티팩트를 갱신한다.

## 품질 기준
- 레퍼런스는 이 서비스와 같은 분야이거나 인접 분야의 실제 상용 서비스다. "깔끔한 디자인" 같은 막연한 말 대신 가져올 점을 구체적으로 적는다.
- 톤 A·B·C 는 사용자가 한눈에 구별할 만큼 다르다.
- 디자인 시스템의 색 대비는 본문 4.5:1, 24px 이상 글자와 아이콘·테두리 3:1 이상이다.
- 모든 산출물이 `.orchestration/design-rules.md` 를 지킨다.

## 하지 않는 것
- 화면 전체 디자인(화면 디자이너가 한다), 앱 코드, 요구사항 변경(필요하면 프리앰블의 ask 명령으로 코디네이터에게 묻는다).
- AskUserQuestion 으로 사용자에게 직접 묻기.
````

- [ ] **Step 5: 설정과 scaffold 를 고친다**

`orca-team/templates/config.toml` 헤더 주석의 `user_approval_roles` 두 줄 뒤에 한 줄을 더한다.

```toml
# director: 디자인 방향(레퍼런스·톤 시안·디자인 시스템). 이 직무가 있으면 orch plan check 가 designer 태스크에 director 선행을 요구한다.
```

같은 파일 `[roles.planner]` 표 뒤, `[roles.designer]` 앞에 넣는다.

```toml
[roles.director]
title = "디자인 디렉터"
claude_model = "opus"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "high"
owns = ["docs/design/**", "assets/**"]
```

`orca-team/lib/orch/setup.py` 의 `SCAFFOLD_FILES` 를 바꾼다.

```python
SCAFFOLD_FILES = [
    ("config.toml", ".orchestration/config.toml"),
    ("coordinator.md", ".orchestration/coordinator.md"),
    ("design-rules.md", ".orchestration/design-rules.md"),
    ("roles/planner.md", ".orchestration/roles/planner.md"),
    ("roles/director.md", ".orchestration/roles/director.md"),
    ("roles/designer.md", ".orchestration/roles/designer.md"),
    ("roles/developer.md", ".orchestration/roles/developer.md"),
    ("roles/tester.md", ".orchestration/roles/tester.md"),
    ("shim.py", ".orchestration/bin/orch"),
]
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_setup -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 7: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 411 tests` … `OK`. 실패가 있으면 이 태스크 변경 때문인지 보고 고친다(템플릿 config 에 director 가 생겨도 기존 그래프 검증은 아직 바뀌지 않았다).

- [ ] **Step 8: 커밋**

```bash
git add -- orca-team/templates/roles/director.md orca-team/templates/design-rules.md orca-team/templates/config.toml orca-team/lib/orch/setup.py orca-team/tests/test_templates.py orca-team/tests/test_setup.py
git commit -m "feat(orca-team): 디자인 디렉터 직무 정의·AI 티 금지 목록·설정을 더한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- orca-team/templates/roles/director.md orca-team/templates/design-rules.md orca-team/templates/config.toml orca-team/lib/orch/setup.py orca-team/tests/test_templates.py orca-team/tests/test_setup.py
```

---

### Task 2: director 의 `orch ask-user` 와 훅

**Files:**
- Modify: `orca-team/lib/orch/ask.py`(모듈 docstring, `ASK_ROLE` → `ASK_ROLES`, `require_planner` → `require_ask_role`, `ask_user`, `open_questions`)
- Modify: `orca-team/lib/orch/cli.py:40`(ask-user 도움말)
- Modify: `orca-team/hooks/orca_team_hook.py:24-26, 84-85, 237-240`
- Modify: `orca-team/templates/worker-spec.md:21`(규칙 5)
- Modify: `orca-team/templates/CLAUDE.section.md:17, 19`
- Test: `orca-team/tests/test_ask.py`, `orca-team/tests/test_hooks.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 1 의 director.md "질문 보내기" 절 이름.
- Produces: `orch.ask.ASK_ROLES = ("planner", "director")`, `orch.ask.require_ask_role(p, task_id) -> dict`. 훅 `ASK_WORKER_RE`(그룹 1 이 직무 이름), `ASK_WORKER_TITLES = {"worker:planner": "기획", "worker:director": "디자인 디렉터"}`. Task 6 의 전체 흐름 테스트가 director 태스크로 `orch ask-user` 를 부른다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_ask.py` 의 `AskUserTest.test_only_working_planner_may_ask` 를 아래 두 테스트로 바꾼다.

```python
    def test_only_working_ask_roles_may_ask(self):
        self.set_task("T-002", role="developer")
        self.set_task("T-003", status="done")
        self.set_task("T-004", role="designer")
        for task_id in ("T-002", "T-003", "T-004", "T-009"):
            proc = self.ask("--json", self.question_file(), task=task_id)
            self.assertEqual(proc.returncode, 2, task_id)
            self.assertIn("ask-user 는 working 상태의 planner·director 태스크만 쓴다", proc.stderr)
        self.assertEqual(self.outbox(), [])
        self.assertEqual(ask.ASK_ROLES, ("planner", "director"))

    def test_director_asks_and_waits_like_planner(self):
        self.set_task("T-002", role="director")
        proc = self.ask("--json", self.question_file(), task="T-002")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        out_id = json.loads(proc.stdout)["id"]
        self.assertEqual((self.outbox()[-1]["type"], self.outbox()[-1]["task"]), ("ask", "T-002"))
        self.orch("answer", out_id, "소셜", env=MAIN)
        out = json.loads(self.ask("--wait", out_id, task="T-002", check=True).stdout)
        self.assertEqual(out, {"id": out_id, "answer": "소셜"})
```

`OpenQuestionsTest` 에 더한다.

```python
    def test_director_ask_is_open_pending_and_reasked(self):
        self.set_task("T-002", role="director")
        self.ask("--json", self.question_file(), task="T-002")  # out-0001, 답 없이 exit 4
        self.assertEqual(self.status(), [{"id": "out-0001", "type": "ask", "task": "T-002"}])
        self.deliver()
        self.assertEqual([e["id"] for e in json.loads(self.orch("reask", env=MAIN).stdout)], ["out-0001"])
        self.orch("answer", "out-0001", "이메일", env=MAIN)
        self.assertEqual(self.status(), [])
        pending = json.loads(self.ask("--pending", task="T-002", check=True).stdout)
        self.assertEqual(pending["pending"], [{"id": "out-0001", "question": "로그인 방식은?", "answer": "이메일"}])
```

`orca-team/tests/test_hooks.py` 의 `test_planner_mark_matches_worker_spec_template` 를 아래로 바꾼다.

```python
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
```

같은 클래스(`test_ask_user_question_deny_points_to_the_right_command` 가 있는 클래스)에 더한다.

```python
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
```

`orca-team/tests/test_templates.py` 의 `test_main_display_rules` 에서 두 곳을 바꾼다.

```python
                    "ask(기획·디자인 디렉터 워커의 질문), approval(디자인 일괄 승인), question(코디네이터 질문. 답은 코디네이터로 간다)",
```

```python
        item = next(line for line in text.splitlines() if line.lstrip().startswith("- ask(기획·디자인 디렉터 워커의 질문)"))
```

같은 파일에 더한다.

```python
    def test_worker_spec_names_ask_roles(self):
        text = (T / "worker-spec.md").read_text(encoding="utf-8")
        self.assertIn("기획(planner)·디자인 디렉터(director) 워커만 직무 정의대로 `orch ask-user` 로 사용자에게 묻는다", text)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_ask test_hooks test_templates 2>&1 | tail -15`
Expected: FAIL. director ask 가 exit 2, `ask.ASK_ROLES`·`ASK_WORKER_RE` 없음, 템플릿 문구 없음.

- [ ] **Step 3: ask.py 를 고친다**

`orca-team/lib/orch/ask.py` 모듈 docstring 을 바꾼다.

```python
"""사용자 직통 질문: 기획·디자인 디렉터 워커 → 메인 질문(ask-user)과 메인의 답(answer).

질문은 outbox 에 `type: "ask"` 줄로 쓰고(메인의 orch watch 가 그대로 전한다), 답은 `state/answers/<out-id>.json` 으로 받는다.
질문한 워커는 답 파일을 2초 간격으로 확인하고, 540초가 지나면 exit 4 로 끝난다(Bash 포그라운드 상한이 10분이다).
"""
```

`ASK_ROLE = "planner"` 줄을 바꾼다.

```python
ASK_ROLES = ("planner", "director")  # 메인을 거쳐 사용자에게 직접 묻는 직무. hooks/orca_team_hook.py ASK_WORKER_RE 와 같게 둔다
```

`require_planner` 함수를 바꾼다.

```python
def require_ask_role(p: Project, task_id: str) -> dict:
    """ask-user 는 working 상태의 ASK_ROLES 태스크만 쓴다."""
    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    st = load_state(p, task_id) or {}
    if st.get("role") not in ASK_ROLES or st.get("status") != "working":
        raise OrchError(f"{task_id}: ask-user 는 working 상태의 {'·'.join(ASK_ROLES)} 태스크만 쓴다"
                        f"(role={st.get('role')}, status={st.get('status')})", 2)
    return st
```

`read_marker` docstring 의 "기획 워커가" 를 "질문한 워커가" 로 바꾼다. `ask_user` 첫 줄 `require_planner(p, task_id)` 를 `require_ask_role(p, task_id)` 로 바꾼다. `answer` docstring 의 "(기획 워커가 받아 간다)" 를 "(질문한 워커가 받아 간다)" 로 바꾼다.

`open_questions` 의 docstring 둘째 항목과 ask 조건을 바꾼다.

```python
    - ask: 답 파일이 없고, 그 태스크가 working 상태의 ASK_ROLES 직무일 때(질문한 워커가 아직 기다린다)
```

```python
            if st.get("role") in ASK_ROLES and st.get("status") == "working" and load_answer(p, out_id) is None:
```

`orca-team/lib/orch/cli.py` 의 ask-user 도움말을 바꾼다.

```python
    s = sub.add_parser("ask-user", help="기획·디자인 디렉터 워커 → 메인 직통 질문과 답 대기(그 워커)")
```

Run: `cd orca-team && grep -rn "ASK_ROLE\b\|require_planner" lib hooks tests`
Expected: 출력 없음.

- [ ] **Step 4: 훅을 고친다**

`orca-team/hooks/orca_team_hook.py` 24~26행(주석, `PLANNER_RE`, `PLANNER_WORKER`)을 바꾼다.

```python
# templates/worker-spec.md 첫 줄 "너는 orca-team 의 {role_title}({role}) 워커다." 의 직무가 orch ask-user 로 사용자에게 직접 묻는
# 직무(orch.ask.ASK_ROLES)면 세션을 worker:<직무> 로 표시한다. 역할 표시는 매 첫 프롬프트에 돌므로 orch.ask 를 불러오지 않고 여기 적는다.
ASK_WORKER_RE = re.compile(r"orca-team 의 [^\n(]*\((planner|director)\) 워커다")
# 세션 표시 → 거부 안내에 쓰는 직무 이름. AskUserQuestion 거부 안내만 다르고 나머지 규칙은 워커와 같다
ASK_WORKER_TITLES = {"worker:planner": "기획", "worker:director": "디자인 디렉터"}
```

`role_mark` 의 워커 분기를 바꾼다.

```python
    elif any(mark in prompt for mark in WORKER_MARKS):
        found = ASK_WORKER_RE.search(prompt)
        role = f"worker:{found.group(1)}" if found else "worker"
```

`guard` 의 기획 워커 분기를 바꾼다.

```python
    if tool == "AskUserQuestion" and role in ASK_WORKER_TITLES:
        return deny(f"{ASK_WORKER_TITLES[role]} 워커는 AskUserQuestion 을 쓰지 않는다. 질문 JSON 을 "
                    "`.orchestration/bin/orch ask-user --task T-xxx --json -` 의 표준 입력으로 보내 메인을 거쳐 사용자에게 묻는다"
                    "(직무 정의 \"질문 보내기\", Bash `timeout: 600000`).")
```

Run: `cd orca-team && grep -n "PLANNER" hooks/orca_team_hook.py`
Expected: 출력 없음.

- [ ] **Step 5: 템플릿 문구를 고친다**

`orca-team/templates/worker-spec.md` 규칙 5 의 마지막 문장을 바꾼다.

- old: ``기획(planner) 워커만 직무 정의대로 `orch ask-user` 로 사용자를 인터뷰한다.``
- new: ``기획(planner)·디자인 디렉터(director) 워커만 직무 정의대로 `orch ask-user` 로 사용자에게 묻는다.``

`orca-team/templates/CLAUDE.section.md` 두 곳을 바꾼다.

- 17행 old: `- 코디네이터·기획 워커 메시지는` → new: `- 코디네이터·기획·디자인 디렉터 워커 메시지는`
- 19행 old: `  - ask(기획 워커의 인터뷰 질문), approval(디자인 일괄 승인)` → new: `  - ask(기획·디자인 디렉터 워커의 질문), approval(디자인 일괄 승인)`

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_ask test_hooks test_templates 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 7: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK`(Task 1 수 + 4)

- [ ] **Step 8: 커밋**

```bash
git add -- orca-team/lib/orch/ask.py orca-team/lib/orch/cli.py orca-team/hooks/orca_team_hook.py orca-team/templates/worker-spec.md orca-team/templates/CLAUDE.section.md orca-team/tests/test_ask.py orca-team/tests/test_hooks.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 디자인 디렉터도 orch ask-user 로 사용자에게 묻는다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- orca-team/lib/orch/ask.py orca-team/lib/orch/cli.py orca-team/hooks/orca_team_hook.py orca-team/templates/worker-spec.md orca-team/templates/CLAUDE.section.md orca-team/tests/test_ask.py orca-team/tests/test_hooks.py orca-team/tests/test_templates.py
```

---

### Task 3: `orch plan check` 의 director 규칙과 기획 지침

**Files:**
- Modify: `orca-team/lib/orch/plan.py`(새 함수 `director_problems`, `check` 에서 호출)
- Modify: `orca-team/tests/helpers.py`(`import re`, `ProjectCase.remove_role`)
- Modify: `orca-team/tests/test_plan.py`(`DIRECTOR_OWNS`, `PlanCase`, 기존 클래스 8개의 부모, 새 `DirectorRuleTest`)
- Modify: `orca-team/templates/roles/planner.md:32-33, 74, 91-129`
- Test: `orca-team/tests/test_plan.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 1 의 템플릿 config `[roles.director]`.
- Produces: `plan.director_problems(tasks: list[PlanTask], cfg: Config, new_keys: set[str] | None = None) -> list[str]`(비순환 그래프에서만 부른다). `ProjectCase.remove_role(role: str) -> None`. `tests/test_plan.py` 의 `DIRECTOR_OWNS`(Task 6 이 쓴다).

- [ ] **Step 1: 테스트 도우미를 더한다**

`orca-team/tests/helpers.py` 위쪽 import 에 `import re` 를 더하고, `ProjectCase` 끝(`scaffold` 뒤)에 메서드를 더한다.

```python
    def remove_role(self, role: str) -> None:
        """config.toml 에서 [roles.<role>] 표를 지운다(그 직무가 없던 이전 버전 프로젝트)."""
        path = self.root / ".orchestration/config.toml"
        text = path.read_text(encoding="utf-8")
        trimmed = re.sub(rf"\n\[roles\.{re.escape(role)}\]\n(?:[^\[\n].*\n|\n)*", "\n", text)
        if trimmed == text:
            raise AssertionError(f"config.toml 에 [roles.{role}] 가 없다")
        path.write_text(trimmed, encoding="utf-8")
```

- [ ] **Step 2: 기존 그래프 테스트를 0.3.0 설정으로 묶는다**

`orca-team/tests/test_plan.py` 의 `write_plan` 함수 뒤에 더한다.

```python
DIRECTOR_OWNS = ["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**",
                 "docs/design/pilots/**", "docs/design/system/**"]


class PlanCase(TaskCase):
    """0.3.0 설정(director 직무 없음)으로 도는 그래프 테스트. director 규칙은 DirectorRuleTest 가 템플릿 설정 그대로 본다."""

    def setUp(self):
        super().setUp()
        self.remove_role("director")
```

그리고 같은 파일의 클래스 8개 부모를 `TaskCase` 에서 `PlanCase` 로 바꾼다: `PlanApplyTest`, `PlanCheckTest`, `PlanReviewTest`, `StartReadyTest`, `ApprovalTest`, `DesignReviewTest`, `GraphEndToEndTest`, `DesignChainTest`.

Run: `cd orca-team && grep -n "^class .*(TaskCase)" tests/test_plan.py`
Expected: `class PlanCase(TaskCase):` 한 줄만 나온다.

- [ ] **Step 3: 실패하는 테스트를 쓴다**

`orca-team/tests/test_plan.py` 끝(`if __name__` 앞)에 더한다.

```python
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
```

`orca-team/tests/test_templates.py` 의 `test_planner_interview_rules` 점검표 항목에서 `"디자인 방향(톤, 참고 자료)"` 를 `"브랜드 자산(로고, 색, 글꼴)과 사용자가 아는 레퍼런스. 없으면 없다고 적는다"` 로 바꾼다.

같은 파일 `test_planner_examples_pass_orch_validation` 끝에 더한다.

```python
        self.assertEqual(plan.director_problems(tasks, cfg), [])
        self.assertEqual([t.role for t in tasks].count("director"), 1)
        self.assertIn("| design-direction | director | - |", confirm["context"])
```

같은 파일에 더한다.

```python
    def test_planner_graph_rules_for_director(self):
        text = (T / "roles/planner.md").read_text(encoding="utf-8")
        for key in ('["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**", '
                    '"docs/design/pilots/**", "docs/design/system/**"]',
                    "시안 화면 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면)", "`docs/design/screens/<화면>/**`",
                    "UI 기반", "director 태스크에 의존한다", "director 가 사용자에게 직접 묻는다", "orch plan check"):
            self.assertIn(key, text)
        self.assertNotIn("디자인 방향(톤, 참고 자료)", text)
```

- [ ] **Step 4: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_plan.DirectorRuleTest test_templates 2>&1 | tail -15`
Expected: FAIL. `test_designer_must_depend_on_director` 등이 exit 0 을 받고, `plan.director_problems` 가 없고, planner.md 문구가 없다. `PlanCase` 로 바꾼 기존 클래스는 통과한다.

- [ ] **Step 5: director 규칙을 구현한다**

`orca-team/lib/orch/plan.py` 의 `graph_problems` 함수 뒤에 더한다.

```python
DIRECTOR_ROLE = "director"
DESIGNER_ROLE = "designer"


def director_problems(tasks: list[PlanTask], cfg: Config, new_keys: set[str] | None = None) -> list[str]:
    """config 에 director 직무가 있고 designer 태스크가 있으면 director 태스크가 정확히 하나이고, designer 태스크가 모두
    그것에 직간접으로 의존해야 한다. new_keys 가 있으면(--append) 그 안의 designer 만 본다(적용한 태스크는 바꿀 수 없다).
    graph_problems 가 빈 목록(없는 key·순환 없음)일 때만 부른다."""
    if DIRECTOR_ROLE not in cfg.roles:
        return []
    designers = [t for t in tasks if t.role == DESIGNER_ROLE and (new_keys is None or t.key in new_keys)]
    if not designers:
        return []
    directors = [t.key for t in tasks if t.role == DIRECTOR_ROLE]
    if len(directors) != 1:
        return [f"designer 태스크가 있으면 director 태스크가 정확히 1개여야 한다(지금: {', '.join(directors) or '없음'}). "
                "디자인 방향(레퍼런스·톤 시안·디자인 시스템)은 director 태스크 하나가 화면 디자인보다 먼저 정한다"]
    director = directors[0]
    above = ancestors({t.key: t.depends_on for t in tasks})
    return [f"{t.key}: designer 태스크는 director 태스크({director})에 직접 또는 간접으로 의존해야 한다. "
            f'depends_on 에 "{director}" 를 넣는다' for t in designers if director not in above[t.key]]
```

`check` 의 `problems += graph_problems(tasks, settled)` 줄을 바꾼다.

```python
    graph = graph_problems(tasks, settled)
    problems += graph
    if not graph:
        problems += director_problems(tasks, cfg, {t.key for t in tasks if t.key not in applied})
```

- [ ] **Step 6: 기획 지침을 고친다**

`orca-team/templates/roles/planner.md` 를 고친다.

점검표 32행 old: `- 디자인 방향(톤, 참고 자료)`
new: `- 브랜드 자산(로고, 색, 글꼴)과 사용자가 아는 레퍼런스. 없으면 없다고 적는다`

점검표 끝(`- 범위 밖` 줄) 뒤에 빈 줄과 문단을 더한다.

```markdown

디자인 톤과 레퍼런스 조사는 묻지 않는다. 레퍼런스 조사와 톤 결정은 디자인 디렉터(director) 태스크에서 director 가 사용자에게 직접 묻는다.
```

확정 질문 예시(74행)의 `"context"` 값을 바꾼다.

```
 "context": "## 기획 핵심\n할 일을 추가·완료·삭제하는 정적 웹앱. 데이터는 브라우저에만 저장한다.\n\n## 자동 결정\n- 정적 HTML 한 장, 빌드 도구 없음\n- 저장은 localStorage\n\n## 태스크 그래프\n| key | 직무 | 선행 |\n|---|---|---|\n| design-direction | director | - |\n| todo-design | designer | design-direction |\n| ui-foundation | developer | design-direction |\n| todo-store | developer | - |\n| todo-ui | developer | todo-design, ui-foundation, todo-store |",
```

tasks.toml 예시 블록(91~124행, ```` ```toml ```` 부터 닫는 ```` ``` ```` 까지)을 바꾼다.

```toml
[[task]]
key = "design-direction"            # 영문 소문자·숫자·하이픈, 파일 안에서 유일
role = "director"                   # config.toml 의 직무
title = "디자인 방향(레퍼런스·톤 시안·디자인 시스템)"   # 한 줄
owns = ["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**", "docs/design/pilots/**", "docs/design/system/**"]
depends_on = []                     # 선행 태스크 key
target = "서비스 전체 디자인 방향과 디자인 시스템"
change = "시안 화면: 대표 화면 할 일 목록, 정보가 빽빽한 화면 주간 통계, 입력 화면 할 일 추가. 레퍼런스 보드, 톤 시안, 디자인 시스템을 사용자 확인을 받아 확정한다"
constraints = "spec.md 화면 목록과 .orchestration/design-rules.md 를 따른다"
acceptance = ["docs/design/direction.md 에 design_system_url 줄이 있다", "docs/design/system/tokens.json 이 있다"]

[[task]]
key = "todo-design"
role = "designer"
title = "할 일 화면 디자인"
owns = ["docs/design/screens/todo/**"]
depends_on = ["design-direction"]
target = "할 일 목록 화면(빈 상태·목록·오류)"
change = "프로젝트 디자인 시스템으로 화면 디자인을 만든다"
constraints = "contracts.md 2절 데이터 형태를 따른다"
acceptance = ["spec.md 화면 목록의 모든 상태가 디자인에 있다"]

[[task]]
key = "ui-foundation"
role = "developer"
title = "UI 기반(토큰·공통 컴포넌트)"
owns = ["src/ui/base/**"]
depends_on = ["design-direction"]
target = "src/ui/base/"
change = "docs/design/system/tokens.json 을 CSS 변수로 옮기고 시스템 컴포넌트를 공통 컴포넌트로 만든다"
constraints = "docs/design/system/README.md 의 사용 규칙을 따른다"
acceptance = ["src/ui/base/tokens.css 가 tokens.json 의 모든 토큰을 담는다"]

[[task]]
key = "todo-store"
role = "developer"
title = "할 일 저장 모듈"
owns = ["src/store/**"]
depends_on = []
target = "src/store/"
change = "localStorage 기반 할 일 저장 모듈"
constraints = "contracts.md 1절 인터페이스를 지킨다"
acceptance = ["node --test src/store 통과"]

[[task]]
key = "todo-ui"
role = "developer"
title = "할 일 화면 구현"
owns = ["src/ui/todo/**"]
depends_on = ["todo-design", "ui-foundation", "todo-store"]
target = "src/ui/todo/"
change = "디자인대로 화면을 구현하고 저장 모듈을 쓴다"
constraints = "contracts.md 1절 인터페이스만 쓰고, 색·크기·간격은 src/ui/base 의 토큰과 공통 컴포넌트만 쓴다"
acceptance = ["index.html 에서 추가·완료·삭제가 된다"]
```

129행을 다섯 줄로 바꾼다.

- old: ``- 화면 디자인(designer) 태스크는 Codex 리뷰 뒤 사용자 일괄 승인을 받는다. 디자인을 입력으로 쓰는 개발 태스크는 `depends_on` 에 디자인 key 를 넣는다.``
- new:

```markdown
- 화면이 있는 서비스면 디자인 방향(director) 태스크 하나를 맨 앞에 둔다. owns 는 `["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**", "docs/design/pilots/**", "docs/design/system/**"]` 이다. `change` 에 시안 화면 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면)를 spec.md 화면 이름으로 적는다.
- 화면 디자인(designer) 태스크는 모두 director 태스크에 의존한다(`depends_on` 에 director key). owns 는 `docs/design/screens/<화면>/**` 이다. Codex 리뷰 뒤 사용자 일괄 승인을 받는다.
- UI 기반 developer 태스크(토큰을 CSS 변수·테마로, 시스템 컴포넌트를 공통 컴포넌트로)는 director 태스크에 의존한다.
- 화면 구현 developer 태스크는 그 화면의 designer 태스크와 UI 기반 태스크에 의존한다. 디자인에 기대지 않는 태스크(저장 모듈, API)는 director 를 기다리지 않는다.
- config.toml 에 director 직무가 있으면 `orch plan check` 가 director 태스크 정확히 1개와, 모든 designer 태스크의 director 의존(직접 또는 간접)을 검사한다.
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_plan test_templates 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 8: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK`(Task 2 수 + 7)

- [ ] **Step 9: 커밋**

```bash
git add -- orca-team/lib/orch/plan.py orca-team/templates/roles/planner.md orca-team/tests/helpers.py orca-team/tests/test_plan.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): director 가 있으면 plan check 가 designer 의 director 선행을 요구한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- orca-team/lib/orch/plan.py orca-team/templates/roles/planner.md orca-team/tests/helpers.py orca-team/tests/test_plan.py orca-team/tests/test_templates.py
```

---

### Task 4: 디자인 자동 검사와 리뷰 프롬프트

**Files:**
- Create: `orca-team/lib/orch/lint.py`
- Create: `orca-team/templates/review-design.md`
- Create: `orca-team/tests/test_lint.py`
- Modify: `orca-team/lib/orch/review.py`(import, 새 함수 `design_section`, `compose_prompt`)
- Modify: `orca-team/templates/review-prompt.md`(기준 4번 뒤 `{design_section}`)
- Test: `orca-team/tests/test_lint.py`, `orca-team/tests/test_review.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 1 의 `.orchestration/design-rules.md` 경로(리뷰 프롬프트가 가리킨다).
- Produces: `orch.lint.DESIGN_ROLES = ("director", "designer", "developer")`, `orch.lint.MAX_FINDINGS = 50`, `orch.lint.design_lint(root: Path, files: list[str]) -> list[str]`(항목 형식 `"<파일>:<줄>: <패턴>(<발췌>)"`, 넘치면 마지막 항목 `"… 외 N건"`). `review.design_section(p, role, files) -> str`. Task 6 의 전체 흐름 테스트가 director 리뷰 프롬프트에 `.orchestration/design-rules.md` 가 있는지 본다.

- [ ] **Step 1: 검사 단위 테스트를 쓴다**

`orca-team/tests/test_lint.py`:

```python
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from orch.lint import DESIGN_ROLES, MAX_FINDINGS, design_lint


class DesignLintTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orca-team-lint-")).resolve()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel

    def heads(self, *rels):
        """"파일:줄: 패턴" 부분만(발췌 앞 첫 괄호까지)."""
        return [item.split("(", 1)[0] for item in design_lint(self.root, list(rels))]

    def test_design_roles(self):
        self.assertEqual(DESIGN_ROLES, ("director", "designer", "developer"))

    def test_emoji_is_reported_once_per_line(self):
        rel = self.put("src/ui/home.tsx", "<h1>홈</h1>\n<p>🚀 빠르게 시작 ✨</p>\n<p>→ 다음 • 항목</p>\n")
        self.assertEqual(design_lint(self.root, [rel]), ["src/ui/home.tsx:2: 이모지(🚀)"])

    def test_left_border_accent_in_css_and_jsx(self):
        css = self.put("src/ui/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n"
                                          ".quote { border-inline-start: 0.25rem solid var(--accent); }\n"
                                          ".table td { border-left: 1px solid #e5e7eb; }\n"
                                          ".none { border-left: 0; }\n")
        jsx = self.put("src/ui/Note.jsx", "const s = { borderLeft: '4px solid #7c3aed', padding: 12 };\n")
        self.assertEqual(design_lint(self.root, [css])[0], "src/ui/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)")
        self.assertEqual(self.heads(css, jsx), ["src/ui/card.css:2: 왼쪽 줄 강조", "src/ui/card.css:4: 왼쪽 줄 강조",
                                                "src/ui/Note.jsx:1: 왼쪽 줄 강조"])

    def test_tailwind_left_border_needs_width_and_color(self):
        rel = self.put("src/ui/Callout.tsx", '<div className="border-l-4 border-blue-500 pl-4">a</div>\n'
                                             '<div className="border-l-4 pl-4">b</div>\n'
                                             '<td className="border-l border-gray-200">c</td>\n')
        self.assertEqual(self.heads(rel), ["src/ui/Callout.tsx:1: 왼쪽 줄 강조"])

    def test_blue_purple_css_gradients(self):
        rel = self.put("src/ui/hero.css", ".hero {\n  background: linear-gradient(\n    135deg,\n    #667eea 0%,\n"
                                          "    #764ba2 100%\n  );\n}\n"
                                          ".ok { background: linear-gradient(#3b82f6, #1d4ed8); }\n"
                                          ".named { background: radial-gradient(circle, blue, purple); }\n"
                                          ".warm { background: linear-gradient(90deg, #f97316, #db2777); }\n")
        self.assertEqual(self.heads(rel), ["src/ui/hero.css:2: 파랑·보라 그라디언트", "src/ui/hero.css:9: 파랑·보라 그라디언트"])

    def test_tailwind_gradient(self):
        rel = self.put("src/ui/Hero.tsx", '<h1 className="bg-gradient-to-r from-indigo-500 to-violet-600">x</h1>\n'
                                          '<div className="bg-gradient-to-r from-blue-500 to-sky-400">y</div>\n')
        self.assertEqual(self.heads(rel), ["src/ui/Hero.tsx:1: 파랑·보라 그라디언트"])

    def test_skips_excluded_unsupported_missing_large_and_non_utf8(self):
        rels = [self.put("docs/planning/spec.md", "🚀\n"), self.put("docs/design/references.md", "🚀\n"),
                self.put("docs/tasks/T-001/report.md", "🚀\n"), self.put("docs/reviews/T-001/r1.md", "🚀\n"),
                self.put("scripts/run.py", "print('🚀')\n"), "src/deleted.css"]
        self.put("src/big.css", "🚀\n" + "a" * (1 << 20))
        (self.root / "src/bad.css").write_bytes(b"\xff\xfe border-left: 4px solid red;\n")
        self.assertEqual(design_lint(self.root, [*rels, "src/big.css", "src/bad.css"]), [])

    def test_caps_findings(self):
        rel = self.put("docs/design/screens/home/copy.md", "".join(f"{n} 🚀\n" for n in range(MAX_FINDINGS + 10)))
        found = design_lint(self.root, [rel])
        self.assertEqual(len(found), MAX_FINDINGS + 1)
        self.assertEqual(found[-1], "… 외 10건")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 리뷰 프롬프트 테스트를 쓴다**

`orca-team/tests/test_review.py` 끝(`if __name__` 앞)에 더한다.

```python
class ReviewDesignSectionTest(ReviewCase):
    """디자인·UI 직무(director, designer, developer) 리뷰 프롬프트에만 디자인 기준과 orch 자동 검사 결과가 들어간다."""

    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")

    def commit_text(self, path, text, task_id="T-001"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"[{task_id}] feat: {path}")

    def test_developer_prompt_has_design_rules_and_lint_warnings(self):
        self.commit_text("src/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n")
        self.review()
        prompt = self.prompt()
        self.assertIn("## 디자인·UI 기준", prompt)
        self.assertIn(".orchestration/design-rules.md", prompt)
        self.assertIn("- src/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)", prompt)
        self.assertNotIn("{design_section}", prompt)
        self.assertNotIn("{design_lint}", prompt)

    def test_clean_change_reports_none(self):
        self.commit_text("src/card.css", ".card { border-left: 1px solid #e5e7eb; }\n")
        self.review()
        prompt = self.prompt()
        self.assertIn("orch 자동 검사", prompt)
        self.assertIn("\n- 없음\n", prompt)

    def test_tester_prompt_has_no_design_section(self):
        self.write_spec("T-002", role="tester", title="로그인 테스트")
        self.git("add", "--", ".orchestration/tasks/T-002.md")
        self.git("commit", "-q", "-m", "[T-002] spec: 로그인 테스트")
        self.orch("start", "T-002")
        self.commit_text("tests/login.test.ts", "// 🚀\n", task_id="T-002")
        self.orch("review", "T-002")
        prompt = self.prompt()
        self.assertIn("[tester]", prompt)
        self.assertNotIn("design-rules.md", prompt)
        self.assertNotIn("orch 자동 검사", prompt)
        self.assertNotIn("{design_section}", prompt)
```

`orca-team/tests/test_templates.py` 의 `test_placeholders` 끝에 더한다.

```python
        self.assertIn("{design_section}", prompt)
        design = (T / "review-design.md").read_text(encoding="utf-8")
        for key in ("{design_lint}", ".orchestration/design-rules.md", "docs/design/system/tokens.json",
                    "## 금지 목록 점검", "## 시스템 추가 제안", "REQUEST_CHANGES"):
            self.assertIn(key, design)
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_lint test_review.ReviewDesignSectionTest test_templates 2>&1 | tail -15`
Expected: FAIL. `ModuleNotFoundError: No module named 'orch.lint'`, 프롬프트에 디자인 절 없음, `review-design.md` 없음.

- [ ] **Step 4: 검사 모듈을 만든다**

`orca-team/lib/orch/lint.py`:

```python
"""디자인 자동 검사(orch review). 태스크가 바꾼 파일에서 AI 티 패턴(.orchestration/design-rules.md 1~3번)의 의심 위치를 찾는다.

결과는 경고다. 블록 인용처럼 정당한 쓰임이 있으므로 불합격 판정은 Codex 가 한다. 표준 라이브러리 정규식만 쓴다."""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

from .globs import matches_any

DESIGN_ROLES = ("director", "designer", "developer")  # 리뷰 프롬프트에 디자인 기준과 이 검사 결과를 넣는 직무
LINT_SUFFIXES = {".html", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".md", ".json"}
LINT_EXCLUDE = ["docs/design/references.md", "docs/planning/**", "docs/tasks/**", "docs/reviews/**"]
MAX_BYTES = 1 << 20  # 이보다 큰 파일(번들·데이터)은 건너뛴다
MAX_FINDINGS = 50  # 리뷰 프롬프트가 커지지 않게 이만큼만 싣는다
SNIPPET = 60

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕]")
BORDER_CSS_RE = re.compile(r"\bborder-(?:left|inline-start)\s*:\s*([^;{}\n]+)", re.I)
BORDER_JS_RE = re.compile(r"\bborder(?:Left|InlineStart)\s*:\s*[\"'`]([^\"'`\n]+)[\"'`]")
WIDTH_RE = re.compile(r"(?<![\w.#-])(\d*\.?\d+)(px|rem|em)\b", re.I)
VISIBLE_RE = re.compile(r"\b(?:solid|dashed|dotted|double|groove|ridge|inset|outset)\b|#[0-9a-f]{3,8}\b"
                        r"|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|color-mix|var)\(", re.I)
TW_LEFT_WIDTH_RE = re.compile(r"(?<![\w-])border-l-(?:[2-8]|\[\d+px\])(?![\w-])")
TW_BORDER_COLOR_RE = re.compile(r"(?<![\w-])border-(?:l-)?(?:[a-z]+-\d{2,3}|black|white|\[#[0-9a-fA-F]{3,8}\])(?![\w-])")
TW_BLUE_RE = re.compile(r"(?<![\w-])(?:from|via|to)-(?:blue|indigo|sky)-\d{2,3}(?![\w-])")
TW_PURPLE_RE = re.compile(r"(?<![\w-])(?:from|via|to)-(?:purple|violet|fuchsia)-\d{2,3}(?![\w-])")
GRADIENT_RE = re.compile(r"\b(?:repeating-)?(?:linear|radial|conic)-gradient\(", re.I)
COLOR_TOKEN_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\([^()]*\)|\b[a-zA-Z]+\b")
BLUE_NAMES = {"blue", "royalblue", "dodgerblue", "deepskyblue", "cornflowerblue", "steelblue", "navy", "mediumblue",
              "darkblue", "midnightblue"}
PURPLE_NAMES = {"purple", "violet", "indigo", "blueviolet", "darkviolet", "mediumpurple", "rebeccapurple", "darkorchid",
                "mediumorchid", "orchid", "plum", "magenta", "fuchsia", "slateblue", "mediumslateblue", "darkslateblue"}


def snippet(text: str) -> str:
    return " ".join(text.split())[:SNIPPET]


def left_border(line: str) -> bool:
    """2px 이상이고 보이는 선(선 모양·색)이 있는 border-left·border-inline-start 선언(CSS, JSX 인라인 스타일),
    또는 한 줄에 Tailwind border-l-[2-8] 과 테두리 색이 함께 있다."""
    for match in (*BORDER_CSS_RE.finditer(line), *BORDER_JS_RE.finditer(line)):
        value = match.group(1)
        widths = [float(number) * (16 if unit.lower() in ("rem", "em") else 1) for number, unit in WIDTH_RE.findall(value)]
        if widths and max(widths) >= 2 and VISIBLE_RE.search(value):
            return True
    return bool(TW_LEFT_WIDTH_RE.search(line) and TW_BORDER_COLOR_RE.search(line))


def parse_color(token: str) -> tuple[float, float, float] | None:
    """(색상각 0~360, 채도 0~1, 밝기 0~1). hex(3·4·6·8자리), rgb(), hsl() 만 읽는다."""
    if token.startswith("#"):
        digits = token[1:]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits[:3])
        elif len(digits) in (6, 8):
            digits = digits[:6]
        else:
            return None
        r, g, b = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
    else:
        numbers = re.findall(r"\d*\.?\d+", token)
        if not token.startswith(("rgb", "hsl")) or len(numbers) < 3:
            return None
        if token.startswith("hsl"):
            return float(numbers[0]) % 360, min(float(numbers[1]), 100) / 100, min(float(numbers[2]), 100) / 100
        r, g, b = (min(float(n), 255) / 255 for n in numbers[:3])
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, sat, light


def hue_family(token: str) -> str | None:
    """색 하나가 파랑 계열이면 "blue", 보라 계열이면 "purple". 무채색에 가깝거나 읽지 못하면 None."""
    low = token.lower()
    if low in BLUE_NAMES:
        return "blue"
    if low in PURPLE_NAMES:
        return "purple"
    color = parse_color(low)
    if color is None:
        return None
    hue, sat, light = color
    if sat < 0.25 or not 0.12 < light < 0.92:
        return None
    if 190 <= hue < 245:
        return "blue"
    if 245 <= hue < 300:
        return "purple"
    return None


def gradient_body(text: str, start: int) -> str:
    """start(여는 괄호 바로 뒤)부터 짝이 맞는 닫는 괄호 앞까지. 닫히지 않으면 500자에서 자른다."""
    depth, end = 1, min(len(text), start + 500)
    for i in range(start, end):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:end]


def scan(rel: str, text: str) -> list[str]:
    """한 파일의 의심 위치를 줄 순서로 "파일:줄: 패턴(발췌)" 목록으로 돌려준다."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.split("\n"), 1):
        emoji = EMOJI_RE.search(line)
        if emoji:
            found.append((number, f"이모지({emoji.group(0)})"))
        if left_border(line):
            found.append((number, f"왼쪽 줄 강조({snippet(line)})"))
        if TW_BLUE_RE.search(line) and TW_PURPLE_RE.search(line):
            found.append((number, f"파랑·보라 그라디언트({snippet(line)})"))
    for match in GRADIENT_RE.finditer(text):
        body = gradient_body(text, match.end())
        if {"blue", "purple"} <= {hue_family(token) for token in COLOR_TOKEN_RE.findall(body)}:
            number = text.count("\n", 0, match.start()) + 1
            shown = snippet(match.group(0) + body + ")")
            found.append((number, f"파랑·보라 그라디언트({shown})"))
    return [f"{rel}:{number}: {what}" for number, what in sorted(found, key=lambda item: item[0])]


def design_lint(root: Path, files: list[str]) -> list[str]:
    """files(저장소 기준 상대 경로) 중 검사 대상의 의심 위치 목록. 대상 확장자가 아니거나, 제외 경로거나, 없거나,
    1MB 를 넘거나, UTF-8 이 아니면 건너뛴다. MAX_FINDINGS 를 넘으면 마지막 항목이 "… 외 N건" 이다."""
    found: list[str] = []
    for rel in files:
        if Path(rel).suffix.lower() not in LINT_SUFFIXES or matches_any(rel, LINT_EXCLUDE):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += scan(rel, text)
    if len(found) > MAX_FINDINGS:
        found = [*found[:MAX_FINDINGS], f"… 외 {len(found) - MAX_FINDINGS}건"]
    return found
```

Run: `cd orca-team/tests && python3 -m unittest test_lint -v 2>&1 | tail -3`
Expected: `OK`. 실패하면 기대값을 바꾸지 말고 정규식·색 판정을 고친다.

- [ ] **Step 5: 리뷰 프롬프트에 넣는다**

`orca-team/templates/review-design.md`:

````markdown
## 디자인·UI 기준
5. `.orchestration/design-rules.md`(AI 티 금지 목록)를 읽고 바뀐 화면·코드가 지키는지 본다. 위반은 REQUEST_CHANGES 사유다. 워커 리포트의 `## 금지 목록 점검` 절이 실제와 맞는지도 본다.
6. 디자인 시스템(`docs/design/system/tokens.json`, `docs/design/system/README.md`)이 있으면 그것을 기준으로 본다. designer 는 시스템에 없는 색·크기·간격·컴포넌트를 새로 만들지 않았는가(필요하면 리포트 `## 시스템 추가 제안` 에 적어야 한다). developer 는 화면 코드가 토큰과 공통 컴포넌트만 쓰고 색·크기·간격·모서리·그림자를 하드코딩하지 않았는가.

orch 자동 검사(의심 위치. 블록 인용처럼 정당한 쓰임도 있으니 직접 열어 보고 판단한다):
{design_lint}
````

`orca-team/templates/review-prompt.md` 의 기준 4번 줄 뒤를 바꾼다.

old:
```
4. 수정 라운드면 이전 리뷰 지적이 모두 반영됐는가.

## REVIEW_FILE 형식
```
new:
```
4. 수정 라운드면 이전 리뷰 지적이 모두 반영됐는가.

{design_section}
## REVIEW_FILE 형식
```

`orca-team/lib/orch/review.py` import 에 한 줄을 더한다.

```python
from .lint import DESIGN_ROLES, design_lint
```

`compose_prompt` 앞에 함수를 더한다.

```python
def design_section(p: Project, role: str, files: list[str]) -> str:
    """디자인·UI 직무 리뷰에 넣는 기준과 orch 자동 검사 결과(templates/review-design.md). 다른 직무는 빈 문자열."""
    if role not in DESIGN_ROLES:
        return ""
    found = design_lint(p.root, files)
    listing = "\n".join(f"- {item}" for item in found) if found else "- 없음"
    return render((TEMPLATES / "review-design.md").read_text(encoding="utf-8"), {"design_lint": listing})
```

`compose_prompt` 의 render 값 dict 마지막 항목 뒤에 `"design_section"` 을 더한다.

```python
        "files": "\n".join(f"- {f}" for f in files),
        "design_section": design_section(p, doc.role, files)})
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_lint test_review test_templates 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 7: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK`(Task 3 수 + 11)

- [ ] **Step 8: 커밋**

```bash
git add -- orca-team/lib/orch/lint.py orca-team/lib/orch/review.py orca-team/templates/review-design.md orca-team/templates/review-prompt.md orca-team/tests/test_lint.py orca-team/tests/test_review.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 디자인·UI 리뷰에 금지 목록 기준과 orch 자동 검사 경고를 넣는다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- orca-team/lib/orch/lint.py orca-team/lib/orch/review.py orca-team/templates/review-design.md orca-team/templates/review-prompt.md orca-team/tests/test_lint.py orca-team/tests/test_review.py orca-team/tests/test_templates.py
```

---

### Task 5: 화면 디자이너·개발자·코디네이터 지침

**Files:**
- Modify: `orca-team/templates/roles/designer.md`(전체 교체)
- Modify: `orca-team/templates/roles/developer.md`(전체 교체)
- Modify: `orca-team/templates/coordinator.md`(금지, 대기 루프, accept 처리, 디자인 일괄 승인, 새 절 "시스템 추가 제안", 직무 선택, 디자인 산출물 규칙)
- Modify: `orca-team/templates/CLAUDE.section.md:35`(위치)
- Modify: `orca-team/skills/init/SKILL.md:39`
- Test: `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 1 의 director.md "보강 태스크" 절과 `design_system_url` 줄, Task 4 의 리뷰 기준(리포트 `## 금지 목록 점검`, `## 시스템 추가 제안` 절 이름).
- Produces: 화면 designer 리포트 절 `## 시스템 추가 제안`(없으면 "없음"), 코디네이터 보강 명령 문구.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_templates.py` 에 더한다.

```python
    def test_designer_uses_project_design_system(self):
        text = (T / "roles/designer.md").read_text(encoding="utf-8")
        for key in ("docs/design/direction.md", "design_system_url", "project/README.md", "design_systems: false",
                    "`## 시스템 추가 제안`", "docs/design/screens/", ".orchestration/design-rules.md",
                    "`## 금지 목록 점검`", "빈 상태, 로딩, 오류", "반응형", "가장 가까운 기존 토큰"):
            self.assertIn(key, text)
        self.assertNotIn("docs/design/system.md", text)

    def test_developer_uses_tokens_and_components_only(self):
        text = (T / "roles/developer.md").read_text(encoding="utf-8")
        for key in ("docs/design/system/tokens.json", "CSS 변수", "공통 컴포넌트", "토큰과 공통 컴포넌트만 쓴다",
                    ".orchestration/design-rules.md", "`## 금지 목록 점검`"):
            self.assertIn(key, text)

    def test_coordinator_handles_director_and_system_proposals(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        for key in ("## 시스템 추가 제안", 'orch task new director "디자인 시스템 보강: <요약>"',
                    "owns: docs/design/system/**, docs/design/direction.md",
                    'orch fix T-xxx --note "디자인 시스템 보강(T-yyy)을 반영한다"',
                    'orch task new developer "토큰 갱신 반영: <요약>"', "- director:", "디자인 디렉터(director) 워커",
                    "아래 \"시스템 추가 제안\""):
            self.assertIn(key, text)
        self.assertLess(text.index("## 시스템 추가 제안"), text.index("## 사용자에게 묻기"))

    def test_role_lists_name_director(self):
        self.assertIn("기본 5개(planner·director·designer·developer·tester)",
                      (PLUGIN / "skills/init/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("`docs/design/direction.md`", (T / "CLAUDE.section.md").read_text(encoding="utf-8"))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates 2>&1 | tail -10`
Expected: FAIL 4개(새 테스트들).

- [ ] **Step 3: 화면 디자이너 지침을 바꾼다**

`orca-team/templates/roles/designer.md` 전체:

````markdown
# designer — 디자인

## 책임
- 앱·웹 화면 디자인. 디자인 디렉터(director)가 확정한 프로젝트 디자인 시스템을 화면에 적용한다.
- 화면에 들어갈 래스터 에셋.

## 입력
- `docs/planning/spec.md`(화면 목록, 흐름), `docs/planning/contracts.md`
- `docs/design/direction.md`: 확정한 디자인 방향과 `design_system_url: <URL>` 줄
- `docs/design/system/`: 디자인 시스템 원본(`README.md`, `tokens.json`, `components/`)
- `.orchestration/design-rules.md`(AI 티 금지 목록)

## 시작
1. `docs/design/direction.md` 에서 `design_system_url:` 줄을 찾는다. 없으면 작업하지 않고 프리앰블의 ask 명령으로 코디네이터에게 묻는다.
2. Artifact 도구를 `action: "read"`, `url: <design_system_url>`, `path: "project/README.md"` 로 불러 시스템 README 를 읽는다. 토큰 값은 `docs/design/system/tokens.json` 을 읽는다.

## 도구 규칙 (고정)
| 산출물 | 도구 |
|---|---|
| 앱·웹 화면(화면, 레이아웃, UI 목업, 와이어프레임, 프로토타입) | Claude Design: Artifact 도구를 `action: "quickstart"`, `intent: "design"`, `design_systems: false` 로 불러 Design 타입을 찾고, 그 `type_url` 로 Design 아티팩트를 만든다. 디자인 시스템은 계정 기본 시스템이 아니라 `design_system_url` 의 프로젝트 시스템을 쓴다 |
| 래스터 에셋(일러스트, 아이콘 비트맵, 배경, 사진풍 이미지) | `/orca-team:image`(Codex). 결과를 직접 열어 보고 검토한 뒤 채택한 파일만 커밋한다 |
| SVG·CSS·다이어그램 | 코드로 직접 작성한다 |

- Codex 이미지 생성으로 화면 전체를 그리지 않는다.
- 화면에 들어갈 래스터 에셋은 먼저 만들어 `assets/` 에 커밋하고, 그다음 Design 아티팩트에서 참조한다.
- Design 타입 `type_url` 은 계정마다 다를 수 있다. 매번 quickstart 로 찾는다.

## 시스템 적용 규칙
- 시스템에 없는 색, 글꼴 크기, 간격, 모서리, 그림자, 컴포넌트를 새로 만들지 않는다.
- 꼭 필요하면 리포트 `## 시스템 추가 제안` 절에 무엇을, 왜, 어느 화면에서 필요한지 적고, 그 화면에는 가장 가까운 기존 토큰과 컴포넌트를 쓴다. 코디네이터가 제안을 모아 director 보강 태스크로 시스템에 더한 뒤 이 화면을 다시 고치게 한다.
- 아이콘은 시스템 README 가 정한 아이콘 세트 하나만 쓴다.
- `.orchestration/design-rules.md` 를 지킨다.

## 산출물
- Design 아티팩트를 채우는 소스 파일: `docs/design/screens/<화면>/`(태스크 owns). Codex 는 claude.ai 아티팩트를 직접 볼 수 없으므로 소스를 커밋해야 리뷰된다.
- 래스터 에셋: `assets/`
- 리포트: Design 아티팩트 URL, 화면 목록, 쓴 디자인 시스템 URL, `## 시스템 추가 제안`(없으면 "없음"), `## 금지 목록 점검`(`.orchestration/design-rules.md` 항목마다 지켰는지, 예외와 근거), `/orca-team:image` 를 썼다면 프롬프트·생성 수·채택 이유

## 품질 기준
- 기획 문서의 화면 목록과 흐름을 빠짐없이 덮는다.
- 모든 화면에 빈 상태, 로딩, 오류 상태와 반응형(모바일·데스크톱 기준폭)을 넣는다.
- 그 서비스에 맞는 현실적인 데이터와 문구로 채운다.
- 개발자가 그대로 구현할 수 있게 쓴 토큰 이름과 컴포넌트 이름을 화면마다 적는다.

## 하지 않는 것
- 디자인 시스템 직접 수정(제안만 한다). 앱 코드 구현. 요구사항 변경(필요하면 ask).
````

- [ ] **Step 4: 개발자 지침을 바꾼다**

`orca-team/templates/roles/developer.md` 전체:

````markdown
# developer — 개발

## 책임
- 기획·디자인 산출물을 코드로 구현한다.

## 입력
- `docs/planning/`
- `docs/design/system/`: 디자인 시스템 원본(`tokens.json`, `README.md`, `components/`). 시스템 URL 은 `docs/design/direction.md` 의 `design_system_url:` 줄이다.
- `docs/design/screens/`(화면 디자인의 Claude Design 소스). Design 아티팩트 URL 은 디자인 태스크 리포트 `docs/tasks/T-xxx/report.md` 에 있다. Artifact 도구 `read` 로 열어 본다.
- `assets/`
- `.orchestration/design-rules.md`(AI 티 금지 목록. UI 작업에 적용한다)

## UI 작업 규칙
- UI 기반 태스크: `docs/design/system/tokens.json` 을 프로젝트의 CSS 변수나 프레임워크 테마로 옮기고, 시스템 컴포넌트(README 와 `components/`)를 공통 컴포넌트로 만든다. 토큰 이름은 시스템 이름을 그대로 쓴다.
- 화면 구현 태스크: 토큰과 공통 컴포넌트만 쓴다. 색, 글꼴 크기, 간격, 모서리, 그림자 값을 코드에 직접 쓰지 않는다. 필요한 토큰이나 컴포넌트가 없으면 만들지 말고 프리앰블의 ask 명령으로 코디네이터에게 묻는다.
- 아이콘은 디자인 시스템이 정한 아이콘 세트만 쓴다.
- `.orchestration/design-rules.md` 를 지킨다. UI 를 바꾼 태스크의 리포트에는 `## 금지 목록 점검` 절(항목마다 지켰는지, 예외와 근거)을 둔다.

## 산출물
- 소유 경로의 코드
- 리포트: 실행·빌드 방법, 검증 명령과 출력 원문

## 품질 기준
- spec 의 Acceptance 를 실제로 실행해 확인하고 출력 원문을 리포트에 붙인다.
- 기존 코드 규칙을 따른다. 관련 없는 리팩터링을 하지 않는다.
- 비밀값을 코드·커밋에 넣지 않는다.
- 단위 테스트는 소유 경로 안에서만 쓴다. `tests/` 는 tester 소유다.

## 하지 않는 것
- 디자인·요구사항 변경(필요하면 ask). 디자인 시스템 토큰 값 바꾸기.
````

- [ ] **Step 5: 코디네이터 지침을 고친다**

`orca-team/templates/coordinator.md` 를 아래 순서로 고친다.

1. 금지 목록
   - old: ``- 기획 인터뷰에 끼어들기. 기획 워커의 `orch ask-user` 질문은 메인으로 바로 간다.``
   - new: ``- 기획·디자인 방향 인터뷰에 끼어들기. 기획(planner)·디자인 디렉터(director) 워커의 `orch ask-user` 질문은 메인으로 바로 간다.``
2. 대기 루프
   - old: `worker_done 없이 죽은 워커(기획 워커 포함)는`
   - new: `worker_done 없이 죽은 워커(기획·디자인 디렉터(director) 워커 포함)는`
3. 리뷰 완료 APPROVE 처리
   - old: ``` `orch start-ready` 를 한 뒤 `orch design-review` 를 해 본다. ```
   - new: ``` `orch start-ready` 를 한 뒤 아래 "시스템 추가 제안"을 확인하고 `orch design-review` 를 해 본다. ```
4. "디자인 일괄 승인" 절 둘째 항목
   - old: ``- 승인 대상 직무 태스크가 모두 `awaiting_user` 이거나 끝나면 `orch design-review`.``
   - new: ``- 승인 대상 직무 태스크가 모두 `awaiting_user` 이거나 끝나면 아래 "시스템 추가 제안"을 먼저 처리한 뒤 `orch design-review`.``
5. "디자인 일괄 승인" 절 끝(`- 디자인에 의존하지 않는 태스크는 승인을 기다리지 않는다. 그래프의 depends_on 만 따른다.` 줄) 뒤, `## 사용자에게 묻기` 앞에 새 절을 넣는다.

```markdown

## 시스템 추가 제안
화면 디자이너(designer) 리포트의 `## 시스템 추가 제안` 절은 곧바로 처리하지 않는다. 화면 designer 태스크가 모두 `awaiting_user` 이거나 끝나면 `orch design-review` 전에 다음을 한다. 제안이 모두 "없음"이면 바로 `orch design-review`.
1. 제안을 모아 director 보강 태스크 하나를 만든다: `orch task new director "디자인 시스템 보강: <요약>"`. spec front matter 에 `owns: docs/design/system/**, docs/design/direction.md` 줄을 넣고, Change 에 제안 원문(태스크 ID, 무엇을, 왜, 어느 화면)을 모두 옮긴다. spec 을 "요청 처리" 2번처럼 경로 지정 커밋하고 `orch start T-yyy`.
2. 보강 태스크가 done 이 되면 제안을 낸 화면마다 `orch fix T-xxx --note "디자인 시스템 보강(T-yyy)을 반영한다"` 하고 `orch start-ready`. 고친 화면이 다시 `awaiting_user` 가 되면 `orch design-review`.
3. UI 기반 developer 태스크가 이미 done 이면 `orch task new developer "토큰 갱신 반영: <요약>"` 을 만든다. front matter 에 `owns:` 로 UI 기반 태스크와 같은 경로를, `depends_on:` 으로 보강 태스크 ID 를 넣는다. 보강 태스크가 done 이 된 뒤 `orch start T-zzz`.
```

6. "직무 선택" 절의 planner 줄 뒤에 director 줄을 넣고 designer 줄을 바꾼다.

```markdown
- director: 디자인 방향. 레퍼런스 보드, 톤 시안, 디자인 시스템(Claude Design)을 `orch ask-user` 로 사용자에게 직접 확인받는다. 기획 뒤 화면 디자인보다 먼저 돈다. 디자인 시스템 보강 태스크도 맡는다
- designer: 앱·웹 화면 디자인(Claude Design, 프로젝트 디자인 시스템 적용), 래스터 에셋(/orca-team:image)
```

7. "디자인 산출물 규칙" 절 첫 항목 앞에 넣는다.

```markdown
- 디자인 방향과 디자인 시스템은 director 가 Claude Design 으로 만든다(`docs/design/direction.md`, `docs/design/system/`). 화면 디자인과 UI 구현은 그 시스템만 쓴다.
```

Run: ``cd orca-team && grep -c -- '-- .orchestration/tasks/T-xxx.md`' templates/coordinator.md``
Expected: `2`(경로 지정 커밋 지시 수가 그대로다)

- [ ] **Step 6: 메인 지침 위치와 init 스킬을 고친다**

`orca-team/templates/CLAUDE.section.md` 35행(`- 디자인: 앱·웹 화면은 ...`) 앞에 한 줄을 넣는다.

```markdown
- 디자인 방향·시스템: `docs/design/direction.md`(`design_system_url:` 줄), `docs/design/system/`(director 가 Claude Design 으로 만든다)
```

`orca-team/skills/init/SKILL.md` 39행
- old: `기본 4개(planner·designer·developer·tester)`
- new: `기본 5개(planner·director·designer·developer·tester)`

- [ ] **Step 7: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_skills 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 8: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK`(Task 4 수 + 4)

- [ ] **Step 9: 커밋**

```bash
git add -- orca-team/templates/roles/designer.md orca-team/templates/roles/developer.md orca-team/templates/coordinator.md orca-team/templates/CLAUDE.section.md orca-team/skills/init/SKILL.md orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 화면 디자인·UI 구현이 프로젝트 디자인 시스템만 쓰고 추가 제안은 director 가 보강한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- orca-team/templates/roles/designer.md orca-team/templates/roles/developer.md orca-team/templates/coordinator.md orca-team/templates/CLAUDE.section.md orca-team/skills/init/SKILL.md orca-team/tests/test_templates.py
```

---

### Task 6: 전체 흐름 테스트, 문서, 버전 0.4.0

**Files:**
- Modify: `orca-team/tests/test_plan.py`(새 `DirectorFlowTest`)
- Modify: `orca-team/lib/orch/paths.py:8`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- Modify: `orca-team/tests/test_cli.py:9`
- Modify: `README.md`(9행 직무 목록, 새 절 "디자인 디렉터", "개발" 절 링크)
- Modify: `docs/superpowers/specs/2026-10-30-orca-team-design.md`(§13 끝에 한 절)
- Test: `orca-team/tests/test_plan.py`, `orca-team/tests/test_cli.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 2(`orch ask-user` director), Task 3(`DIRECTOR_OWNS`, director 규칙), Task 4(리뷰 프롬프트 디자인 절).

- [ ] **Step 1: 전체 흐름 테스트를 쓴다**

`orca-team/tests/test_plan.py` 의 `DirectorRuleTest` 뒤에 더한다.

```python
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
```

- [ ] **Step 2: 버전·문서 테스트를 쓴다**

`orca-team/tests/test_cli.py` 9행 old: `"orca-team 0.3.0"` → new: `"orca-team 0.4.0"`.

`orca-team/tests/test_templates.py` 에 더한다.

```python
    def test_readme_documents_design_director(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("## 디자인 디렉터", "planner·director·designer·developer·tester", "orch ask-user", "design_system_url",
                    ".orchestration/design-rules.md", "시스템 추가 제안", "`[roles.director]`",
                    "0.3.0 으로 만든 프로젝트는", "orca-team/templates/roles/director.md",
                    "orca-team/templates/design-rules.md", "orch plan check",
                    "docs/superpowers/specs/2026-10-31-orca-team-design-director-design.md",
                    "docs/superpowers/plans/2026-10-31-orca-team-design-director.md"):
            self.assertIn(key, text)

    def test_spec_section_13_design_director(self):
        text = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = text[text.index("## 13."):]
        for key in ("### 디자인 디렉터 (2026-10-31, 0.4.0)", "ASK_ROLES", "director_problems", "design_lint",
                    "review-design.md", "--append"):
            self.assertIn(key, section)
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_plan.DirectorFlowTest test_cli test_templates 2>&1 | tail -10`
Expected: `DirectorFlowTest` 는 통과한다(Task 2~4 가 끝났다). 실패하면 앞 태스크의 결함이므로 원인을 먼저 고친다. `test_version`, `test_readme_documents_design_director`, `test_spec_section_13_design_director` 는 FAIL.

- [ ] **Step 4: 버전을 올린다**

- `orca-team/lib/orch/paths.py` 8행: `VERSION = "0.4.0"`
- `orca-team/.claude-plugin/plugin.json`: `"version": "0.4.0"`
- `.claude-plugin/marketplace.json` 의 orca-team 항목: `"version": "0.4.0"`

- [ ] **Step 5: README 를 고친다**

`README.md` 9행
- old: `직무 워커(planner·designer·developer·tester)`
- new: `직무 워커(planner·director·designer·developer·tester)`

같은 파일 `## 보안 주의` 앞에 새 절을 넣는다.

```markdown
## 디자인 디렉터

- 기획이 확정되면 디자인 디렉터(director) 태스크가 화면 디자인보다 먼저 돈다. director 는 기획 워커처럼 `orch ask-user` 로 메인을 거쳐 사용자에게 직접 묻는다.
  1. 레퍼런스: 같은 분야·인접 분야 상용 서비스 6~9개를 조사하고 Orca 내장 브라우저로 실제 화면을 캡처한다(저장소 밖 `~/.cache/orca-team/refs/<프로젝트>/`). Claude Design 레퍼런스 보드에 스타일 묶음 3~4개로 정리하고 사용자가 묶음을 고른다.
  2. 톤 시안: 시안 화면 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면)를 톤 3가지로 그린 9장을 보여 준다. 세부 수정 피드백이면 수정안 1개, 방향을 바꾸는 피드백이면 새 3안으로 확정할 때까지 반복한다.
  3. 디자인 시스템: 확정한 톤으로 Claude Design Design System 문서(토큰, 상태별 컴포넌트 미리보기, 사용 규칙)를 만들어 확인받는다. 원본은 `docs/design/system/`, URL 은 `docs/design/direction.md` 의 `design_system_url:` 줄이다.
- 화면 디자이너는 이 프로젝트 디자인 시스템만 쓴다. 시스템에 없는 값이 필요하면 리포트에 "시스템 추가 제안"을 적고, 코디네이터가 디자인 일괄 승인 전에 모아 director 보강 태스크로 처리한다. 개발은 `tokens.json` 을 CSS 변수·테마로 옮긴 UI 기반 태스크의 토큰과 공통 컴포넌트만 쓴다.
- AI 티 금지 목록 `.orchestration/design-rules.md`(고정 이모지, 왼쪽 줄 카드, 파랑·보라 그라디언트 등 10개)를 director·designer·developer 가 지키고 리포트에 점검 결과를 적는다. Codex 리뷰가 이 목록으로 판정하고, `orch review` 는 Codex 를 부르기 전에 바뀐 파일에서 이모지·왼쪽 줄 강조·파랑·보라 그라디언트 의심 위치를 찾아 리뷰 프롬프트에 경고로 넣는다(자동 불합격은 아니다).
- config.toml 에 `[roles.director]` 가 있으면 `orch plan check` 가 director 태스크 정확히 1개와, 모든 designer 태스크의 director 의존(직접 또는 간접)을 요구한다. `--append` 는 새로 더하는 designer 만 본다.
- 0.3.0 으로 만든 프로젝트는 config.toml 에 `[roles.director]` 가 없어 지금 흐름 그대로 돈다. 새 흐름을 쓰려면 다음을 한다.
  1. `.orchestration/config.toml` 에 `orca-team/templates/config.toml` 의 `[roles.director]` 블록을 복사해 넣는다.
  2. `orca-team/templates/roles/director.md` 를 `.orchestration/roles/director.md` 로, `orca-team/templates/design-rules.md` 를 `.orchestration/design-rules.md` 로 복사한다.
  3. `.orchestration/roles/planner.md`, `designer.md`, `developer.md` 와 `.orchestration/coordinator.md` 를 `orca-team/templates/` 의 같은 파일로 바꾸고, `CLAUDE.md` 의 `<!-- orca-team -->` 절을 `orca-team/templates/CLAUDE.section.md` 로 바꾼다. 코디네이터 터미널을 닫고 `/orca-team:up` 으로 다시 띄운다.
```

"개발" 절의 설계·구현 계획 두 줄을 바꾼다.

```markdown
- 설계: `docs/superpowers/specs/2026-10-30-orca-team-design.md`, 그릴링 기획·병렬 실행 `docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md`, 디자인 디렉터 `docs/superpowers/specs/2026-10-31-orca-team-design-director-design.md`
- 구현 계획: `docs/superpowers/plans/2026-10-30-orca-team.md`, `docs/superpowers/plans/2026-10-31-orca-team-grilling-parallel.md`, `docs/superpowers/plans/2026-10-31-orca-team-design-director.md`
```

- [ ] **Step 6: 기반 설계 §13 에 절을 더한다**

`docs/superpowers/specs/2026-10-30-orca-team-design.md` 끝(§13 의 "실환경 확인 반영" 절 뒤)에 더한다.

```markdown

### 디자인 디렉터 (2026-10-31, 0.4.0)

설계: `2026-10-31-orca-team-design-director-design.md`. 구현에서 정한 것:
- `orch ask-user` 허용 직무는 `ask.ASK_ROLES = ("planner", "director")` 다. 훅은 정규식 `ASK_WORKER_RE` 로 `worker:planner`·`worker:director` 를 표시하고, 테스트가 둘을 묶는다.
- `plan.director_problems` 는 config 에 director 가 있을 때만 돈다. director 개수는 tasks.toml 전체로 세고, director 의존은 새로 더하는 designer 만 본다(`--append` 는 적용한 태스크를 바꿀 수 없다).
- `lint.design_lint` 는 이모지, 2px 이상 왼쪽 줄 강조(CSS·JSX 인라인·Tailwind), 파랑·보라 그라디언트(색상각 파랑 190~245°, 보라 245~300°, Tailwind from·via·to)를 경고로만 찾는다. 50건에서 자른다.
- 리뷰 프롬프트는 `{design_section}` 자리에 디자인 직무(director·designer·developer)만 `templates/review-design.md` 를 채운다.
- director 보강 태스크는 코디네이터가 spec front matter `owns:` 로 소유 경로를 좁힌다.
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_plan test_cli test_templates 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 8: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK`(Task 5 수 + 3)

- [ ] **Step 9: 커밋**

```bash
git add -- orca-team/tests/test_plan.py orca-team/tests/test_cli.py orca-team/tests/test_templates.py orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json README.md docs/superpowers/specs/2026-10-30-orca-team-design.md
git commit -m "docs(orca-team): 디자인 디렉터 흐름을 README·설계에 적고 0.4.0 으로 올린다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- orca-team/tests/test_plan.py orca-team/tests/test_cli.py orca-team/tests/test_templates.py orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json README.md docs/superpowers/specs/2026-10-30-orca-team-design.md
```

---

### Task 7: 실환경 확인(수동, 컨트롤러가 직접 한다)

이 태스크는 서브에이전트에게 맡기지 않는다. 실제 Orca, opus 워커, Claude Design 을 쓰므로 시작 전에 사용자에게 진행 여부를 묻는다. 사용자가 원하지 않으면 건너뛰고 그 사실을 최종 보고에 적는다.

- [ ] **Step 1: 시험 프로젝트를 만든다**

빈 시험 프로젝트 폴더(`<시험 프로젝트>`)를 만들고 Orca 터미널에서 Claude 세션을 연다. `/orca-team:init 동네 도서관 좌석 예약 웹앱. 한국어 서비스.` 로 세팅한다. `.orchestration/roles/director.md`, `.orchestration/design-rules.md`, config `[roles.director]` 가 생겼는지 본다.

- [ ] **Step 2: 기획에서 director 그래프를 확인한다**

메인에게 요청을 넘기고 기획 인터뷰에 답한다. 확정 질문의 태스크 그래프 표에 director 가 맨 앞에 있고 designer·UI 기반이 director 에 의존하는지, `orch plan check` 가 통과하는지 본다.

- [ ] **Step 3: director 세 단계를 확인한다**

- 레퍼런스: 메인이 `render.context`(보드 링크·묶음 요약)를 먼저 보여 주고 묻는가. `~/.cache/orca-team/refs/<시험 프로젝트 폴더 이름>/` 에 PNG 가 있는가. 사용자 탭이 닫히지 않았는가. `docs/design/references.md` 가 커밋됐는가.
- 톤 시안: 9장 캔버스 링크가 오는가. Other 로 세부 수정 피드백을 주면 수정안 1개가 오는가.
- 디자인 시스템: Design System 문서가 열리고 컴포넌트 상태 미리보기가 보이는가. 확정 뒤 `docs/design/direction.md` 에 `design_system_url:` 줄, `docs/design/system/tokens.json` 이 커밋됐는가.

- [ ] **Step 4: 리뷰와 후속 시작을 확인한다**

director 리뷰 프롬프트(`~/.cache/orca-team/runs/<시험 프로젝트 폴더 이름>/T-*/prompt.txt`)에 "## 디자인·UI 기준" 과 자동 검사 결과가 있는가. accept 뒤 화면 designer 와 UI 기반 developer 가 함께 시작하는가. 화면 designer 가 프로젝트 시스템(`design_system_url`)으로 캔버스를 만드는가.

- [ ] **Step 5: 결과를 기록하고 정리한다**

발견한 결함을 `.superpowers/sdd/gp/director-e2e-report.md` 에 F1, F2… 로 적고 사용자에게 고칠지 묻는다. 끝나면 사용자 동의를 받아 시험 프로젝트의 Orca 탭·터미널을 닫고 폴더를 휴지통으로 옮긴다.
