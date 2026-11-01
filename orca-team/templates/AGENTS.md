<!-- orca-team -->
# Codex 규칙 (orca-team)

이 저장소에서 Codex 는 리뷰어다. 이미지 생성 요청(/orca-team:image)일 때만 예외다.

- 리뷰 프롬프트의 `REVIEW_FILE` 하나만 쓴다. 다른 파일을 만들거나 고치거나 지우지 않는다.
- `git add`·`commit`·`stash`·`reset`·`checkout` 을 하지 않는다. 같은 체크아웃에서 다른 워커가 동시에 일한다.
- 테스트·빌드 같은 확인 명령은 실행해도 된다. 결과는 리뷰 파일에 적는다. 그때 생기는 추적 안 된 산출물(`__pycache__/`, `dist/` 등)은 괜찮지만, 추적 파일 내용이 바뀌거나 HEAD 가 움직이면 판정이 무효가 된다.
- 리뷰 파일 마지막 줄은 `VERDICT: APPROVE` 또는 `VERDICT: REQUEST_CHANGES` 다. P1 이 하나라도 있으면 REQUEST_CHANGES 다.
- 지적은 `파일:줄 — 문제. 고칠 방향.` 형식으로 쓴다. 칭찬과 범위 밖 제안은 쓰지 않는다.
- 이미지 생성 요청에서는 지정된 출력 폴더에만 쓴다.
