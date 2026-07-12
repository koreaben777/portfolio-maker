# Portfolio Maker 일반형 HTML 디자인 선택

날짜: 2026-07-13
상태: A 선택 및 구현 기준

## 공통 brief

- 방문자는 프로젝트, 검증된 claim, evidence provenance를 빠르게 확인한다.
- Portfolio Maker가 만든 build-time public manifest만 입력으로 사용한다.
- raw path, snapshot path, SQLite, private source, credential는 UI와 bundle에 포함하지 않는다.
- 프로젝트 상세에는 evidence의 날짜와 activity type을 이용한 timeline을 표시한다.

## 비교한 세 시안

1. **A / Evidence-led editorial**: warm white, near-black type, cobalt accent, 비대칭 project index와 evidence rail.
2. **B / Quiet dossier**: cool gray, compact table rhythm, provenance를 행 단위로 우선 배치.
3. **C / Signal map**: dark canvas, high-contrast nodes, timeline을 시각적 sequence로 강조.

## 선택

사용자는 **A**를 선택했다. A는 반복 열람과 evidence 비교에 적합한 넓은 project index를 제공하고, 선택한 프로젝트의 detail panel에 timeline을 함께 노출한다. 모션은 색상·background transition과 keyboard focus 상태처럼 상태 변화를 보조하는 범위로 제한하고, `prefers-reduced-motion`에서는 즉시 전환한다.

## 구현 경계

`web/portfolio`는 vanilla TypeScript/Vite 정적 surface다. `portfolio-maker render-html`이 manifest를 generated data module로 번들하고, output 검증 후 `.portfolio-maker/artifacts/portfolio.html`을 만든다. hosting은 이 작업에서 실행하지 않는다.
