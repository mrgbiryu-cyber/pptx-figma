# PPTX -> Figma Architecture V2

이 문서는 현재의 slide-by-slide 보정 루프를 중단하고, 공통 아키텍처로 재정렬하기 위한 기준 문서다.

현재 판단:
- Slide 12 / 19는 사실상 닫힘
- 남은 병목은 Slide 29의 `dense_ui_panel`
- 따라서 남은 작업은 `29를 고치는 것`이 아니라, `dense_ui_panel` 공통 렌더러를 세우는 것이다

## 1. 왜 기존 방식이 한계였나

기존 방식:
- `PPT intermediate -> block bundle -> 시각 보정`

문제:
- semantic table과 visual lane을 같은 것으로 취급
- owner / containment / z-order가 렌더 단계에서 뒤늦게 섞임
- slide 29 같은 좁은 UI 패널에서 작은 흔들림이 전체 레이아웃 붕괴로 이어짐
- 결과적으로 구조 변경이 시각 변화로 잘 이어지지 않음

결론:
- `슬라이드별 튜닝`으로는 스케일 불가
- `공통 패턴 엔진`으로 올라가야 함

## 2. 목표 구조

권장 파이프라인:

`PPTX -> Raw Extractor -> Resolved PPT Model -> IR -> Pattern Renderer -> Figma Emitter`

### 2.1 Raw Extractor

역할:
- OOXML/DrawingML을 가능한 사실 그대로 읽기

산출:
- bounds
- transforms
- text body/run
- table grid/span
- connector adjust
- image blipFill
- placeholder / source_scope

### 2.2 Resolved PPT Model

역할:
- master/layout/slide 상속 해제
- group transform 반영
- EMU -> px 정규화

즉 이 단계 이후에는:
- 렌더를 위해 다시 master/layout을 거슬러 올라가지 않음
- 모든 노드는 `visual-ready source`가 되어야 함

### 2.3 IR

역할:
- renderer가 직접 소비하는 안정적인 중간 스키마

핵심 필드:
- `atom_type`
- `owner_id`
- `layer_role`
- `z_index`
- `clip_scope`
- `render_mode`
- `pattern_type`
- `source_bounds_px`
- `visual_bounds_px`

현재 구현 시작점:
- [build_resolved_ppt_ir.py](/mnt/c/Users/mrgbi/OneDrive/Desktop/cnsatlas/scripts/build_resolved_ppt_ir.py)

### 2.4 Pattern Renderer

슬라이드별이 아니라 패턴별로 렌더한다.

현재 패턴:
- `flow-process`
- `table-heavy`
- `dense_ui_panel`

특히 `dense_ui_panel`은 다음 레이어 순서를 고정한다.
1. `background/base`
2. `cards`
3. `text rows / lanes`
4. `small assets / icons`
5. `overlay marks`

### 2.5 Figma Emitter

우선순위:
- native `TEXT`
- native `RECTANGLE/FRAME`
- `VECTOR`
- 어려운 부분만 `SVG_BLOCK` / image fallback

원칙:
- 검색/편집 가능한 텍스트는 native 우선
- SVG는 전체 fallback이 아니라 부분 fallback

## 3. Pattern별 해석

### 3.1 flow-process

핵심 객체:
- connector
- decision diamond
- process box
- step stack
- header band

주요 규칙:
- connector는 `shape_kind + connection idx + adjust` 기반 archetype
- box/label은 native 노드 우선

### 3.2 table-heavy

핵심 객체:
- table
- merged label
- overlay connector
- note/header

주요 규칙:
- semantic table 유지
- render는 `grid + text` 혹은 lane 기반
- merged label은 별도 owner

### 3.3 dense_ui_panel

핵심 객체:
- version stack
- issue card
- description cards
- description text lanes
- small assets
- meta table

주요 규칙:
- semantic table != visual render
- description은 `table-backed lane`
- small asset은 generic rectangle fallback 금지
- z-order는 고정 규칙 적용

## 4. 구현 기준

### 유지할 것
- current PPT extractor
- block / owner 실험에서 얻은 패턴 지식
- reference JSON
- comparison bundles

### 버릴 것
- slide 29 전용 patch
- 결과를 보며 즉석 위치 보정
- 같은 요소를 table와 lane에 이중 렌더하는 구조

### 새로 만들 것
1. `resolved IR`
2. `pattern renderer`
3. `owner graph`
4. `layer order policy`

## 5. 현재 전환 단계

지금은 architecture v2로 넘어가는 첫 단계다.

완료:
- 조사 내용 문서화
- `Resolved IR` builder 골격 추가

다음:
1. IR 산출 확인
2. dense_ui_panel owner graph 정리
3. dense_ui_panel renderer를 `29`로 검증
4. 12/19는 회귀 테스트로만 확인

## 6. 산출물

- 조사 노트:
  - [pptx-figma-technical-research-notes.md](/mnt/c/Users/mrgbi/OneDrive/Desktop/cnsatlas/docs/pptx-figma-technical-research-notes.md)
- IR builder:
  - [build_resolved_ppt_ir.py](/mnt/c/Users/mrgbi/OneDrive/Desktop/cnsatlas/scripts/build_resolved_ppt_ir.py)

## 7. 결론

현재 프로젝트의 다음 단계는 `29를 계속 고치는 것`이 아니다.

정답은:
- `resolved model`
- `IR`
- `pattern renderer`

를 세우고, `29`는 그 `dense_ui_panel` 패턴의 검증 샘플로만 쓰는 것이다.
