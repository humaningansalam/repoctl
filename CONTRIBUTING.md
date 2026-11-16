# Contributing to repoctl

한국어 또는 영어로 이슈와 PR을 작성할 수 있습니다. 변경의 목적과 실제로 확인한 결과를 짧고 구체적으로 남겨 주세요.

## 범위와 접수처

이 저장소는 CLI, Task lifecycle, Handoff freshness, Graph·Knowledge 탐색과 관련 계약을 관리합니다. 문서 작성 양식 자체는 별도 [agent-handoff-template](https://github.com/humaningansalam/agent-handoff-template)에서 관리합니다. 모델 선택, 에이전트 실행, 제품 배포는 repoctl의 책임이 아닙니다.

Bug 양식에는 문제·기대 결과·최소 재현을, Feature 양식에는 실제 불편·원하는 사용 결과를 적습니다. 실행 명령과 관련 버전이 유용하지만 원인 분석은 필수가 아닙니다. Task 종료 문제에는 익명화된 Chosen·상태·오류의 최소 예시를, Graph 문제에는 질의와 기대한 구현·테스트 관계를, Knowledge 문제에는 원본과 검색 결과의 차이를 적습니다. 모든 유형에 모든 정보를 요구하지 않습니다.

토큰·개인정보·비공개 저장소 내용은 제거하세요. 전체 workspace나 원시 로그를 올리거나 재현을 통과시키려고 원본 Task·receipt·Git 이력을 수정하지 마세요. 동작 실험과 종료 명령은 버릴 수 있는 복사본에서 실행합니다.

## 소스 개발과 검증

독립 repoctl **소스 checkout의 루트**에서 실행합니다. adopter workspace나 설치된 배포 디렉터리는 소스 테스트 위치가 아닙니다.

```sh
uv sync --locked --extra structural
uv run --locked --extra structural pytest -q tests/repoctl/task/test_task_path_scoped_ack.py
uv run --locked --extra structural pytest -q
git diff --check
```

위 집중 테스트는 예시입니다. 변경한 기능의 기존 테스트를 선택하고, 버그 수정에는 수정 전 실패·수정 후 성공을 확인하는 사례와 관련 음성 사례를 포함합니다. 전체 suite의 검색 검사는 `rg`가 필요합니다. Dart 등 선택 분석기의 환경상 skip과 실제 실패를 구분해 기록합니다. 실제 CI 명령은 [.github/workflows/ci.yml](.github/workflows/ci.yml)에 있습니다.

문서·이슈·PR 양식만 바꾼 경우에는 YAML 문법, 링크, 필수 질문과 실제 기본 브랜치의 적용 위치를 확인합니다. 실행하지 않은 제품 테스트를 통과했다고 적지 않습니다.

`ARTIFACT-MANIFEST.json`은 공개 runtime payload를 고정합니다. 양식과 이 기여 안내는 소스 저장소 운영 파일이며 runtime 설치 대상이 아닙니다. payload가 바뀌지 않았다면 다음 명령으로 기존 배포물의 동일성을 확인할 수 있습니다.

```sh
python3 .github/scripts/build_release_archive.py
```

runtime이나 배포 계약을 바꾼 경우에는 별도의 새 버전과 release 검증이 필요합니다. 이미 공개된 태그·asset을 덮어쓰지 않습니다. 저장소 운영 양식만 바뀌면 runtime 재릴리스나 기존 adopter 재설치는 필요하지 않습니다.

## 작업 기록을 중복하지 않습니다

**별도 GitHub Task 양식은 두지 않습니다.** 사용자 workspace의 Task·Board는 현재 실행 범위와 인계의 원본입니다. GitHub 이슈는 공유할 문제·요청, PR은 구현 판단과 검토, CI는 실제 검사 결과를 담습니다. 관련 번호나 링크로 연결하고 Task 상태·Handoff·receipt를 복제하거나 자동 동기화하지 않습니다.

문서 수정, 합의된 유지보수, 양식에 맞지 않는 질문은 빈 이슈나 바로 PR로 작성할 수 있습니다. 작은 변경에 이슈 생성을 강제하지 않습니다. 비공개 Task ID와 전체 기록은 공개할 필요가 없습니다.

## PR 작성과 병합

한 PR은 하나의 논리적 변경과 그 테스트·필요 문서를 함께 다룹니다. 관계없는 포맷 변경이나 의존성 갱신은 분리합니다. 짧은 작업 브랜치를 사용하고 기존 명명 관행을 불필요하게 바꾸지 않습니다. 예: `fix-42-handoff-link`, `docs-contribution-guide`.

제목은 `fix(tasks): ...`, `feat: ...`, `docs: ...`, `chore: ...`처럼 최종 변경을 설명합니다. 본문에는 변경 이유, 실제 검증, 관련 작업과 필요한 영향만 남깁니다. CI 통과 선언 체크박스와 파일별 작업 일지는 추가하지 않습니다.

병합 전 최신 diff와 실제 CI를 확인하고 PR 제목·본문을 정리합니다. 일반 변경은 Squash를 사용하되, 적용 중인 보호 규칙을 우회하거나 존재하지 않는 필수 검사·독립 리뷰어를 가정하지 않습니다. PR 병합, 릴리스 게시, adopter 적용, 제품 수락은 각각 구분합니다.

## 기존 라벨을 활용한 분류

현재의 `bug`, `enhancement`, `documentation` 라벨을 각각 버그, 사용 흐름 개선, 문서 중심 변경에 사용합니다. 양식은 `bug` 또는 `enhancement`만 기본 적용합니다. `[Bug]` 제목 접두사, 별도 Task 분류, 우선순위 자기평가를 중복 요구하지 않습니다. 기존 라벨이나 과거 이슈를 일괄 삭제·재분류하지 않습니다.

검토 후 우선순위가 필요하면 근거를 남기고, 정보 대기에는 필요한 정보, 차단에는 선행 조건과 해제 조건을 적습니다. 정상 해결은 닫고, 중복·미채택·재현 불가는 이유와 관련 이슈를 남깁니다. 추가 라벨·보드·자동화는 반복되는 실제 필요가 있을 때만 도입합니다.
