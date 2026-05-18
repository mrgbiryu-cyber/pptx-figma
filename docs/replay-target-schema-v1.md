# Replay Target Schema v1

## 목적

이 문서는 `PPT -> replay-grade Figma JSON` 생성기의 타깃 스키마를 고정한다.

현재 기준:

- 완료된 visual renderer는 `figma-replay-bundle -> renderFigmaReplayBundle() -> renderReplayNode()` 경로다.
- 따라서 새 generator의 목표는 **PPT를 이 renderer가 그대로 먹을 수 있는 구조로 만드는 것**이다.

즉 목표 파이프라인은 아래다.

- `PPT`
- `-> PPT parser`
- `-> replay-grade JSON generator`
- `-> figma-replay-bundle`
- `-> renderFigmaReplayBundle()`

## 테스트 기준

테스트는 항상 아래 2가지를 동시에 본다.

1. `PPT를 파싱했을 때 replay-grade JSON이 잘 생성되는가`
2. `그 JSON이 완료 renderer에서 고급 플러그인 수준에 가까운가`

## 타깃 입력 형식

최종 renderer가 받는 최소 최상위 구조는 아래다.

```json
{
  "kind": "figma-replay-bundle",
  "source_file": "optional",
  "file_name": "optional",
  "page_name": "optional",
  "node_id": "root node id",
  "document": {},
  "assets": {},
  "missing_assets": []
}
```

필수 필드:

- `kind`
- `node_id`
- `document`
- `assets`

## document 루트 규칙

`document`는 반드시 `FRAME` 또는 `GROUP` 루트여야 한다.

권장:

- 전체 문서 루트는 `FRAME`
- 각 페이지도 `FRAME`

최소 예시:

```json
{
  "id": "pptx:document",
  "type": "FRAME",
  "name": "Replay Document",
  "absoluteBoundingBox": { "x": 0, "y": 0, "width": 3000, "height": 1200 },
  "relativeTransform": [[1, 0, 0], [0, 1, 0]],
  "fills": [],
  "strokes": [],
  "strokeWeight": 0,
  "children": []
}
```

## 공통 node 최소 스키마

모든 node에 공통으로 필요한 필드:

- `id`
- `type`
- `name`
- `absoluteBoundingBox`
- `relativeTransform`
- `children`

권장 공통 필드:

- `fills`
- `strokes`
- `strokeWeight`
- `debug`

### absoluteBoundingBox

필수 구조:

```json
{
  "x": 0,
  "y": 0,
  "width": 100,
  "height": 24
}
```

규칙:

- 렌더 기준 절대 배치값
- `width`, `height`는 1 이상

### relativeTransform

필수 구조:

```json
[[1, 0, 0], [0, 1, 0]]
```

규칙:

- 회전/뒤집힘은 이 필드로 표현
- 초기 generator 단계에서도 identity만 쓰지 말고 가능한 범위에서 실제 값 생성

## 타입별 최소 스키마

### FRAME

필수:

- `fills`
- `strokes`
- `strokeWeight`
- `children`

용도:

- 페이지
- 큰 그룹 블록
- 테이블
- labeled shape wrapper

### GROUP

필수:

- `children`

용도:

- 비주얼상 묶음이지만 shell이 필요 없는 그룹
- diamond + text 조합

### TEXT

필수:

- `characters`
- `fills`
- `style`

`style` 최소 필드:

- `fontSize`
- `fontFamily`
- `textAlignHorizontal`
- `textAlignVertical`
- `textAutoResize`

권장:

- `lineHeightPx`
- `textRuns`

`textRuns`:

```json
[
  {
    "start": 0,
    "end": 4,
    "characters": "Cell",
    "style": {
      "fontSize": 8,
      "fontFamily": "Malgun Gothic",
      "fills": [{"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0}, "opacity": 1}]
    }
  }
]
```

Rules:

- `start` and `end` are UTF-16 string offsets in `characters`
- renderer applies range style to native Figma TextNode
- base `fills` and `style` remain required as fallback
- mixed PPT text colors must be preserved here, not flattened to one cell color

### RECTANGLE

필수:

- `fills`
- `strokes`
- `strokeWeight`

선택:

- `cornerRadius`

용도:

- box
- image container
- table cell background

### EDITABLE_TABLE

Required:

- `columns`
- `rows`
- `cells`

Purpose:

- editable table surrogate for Figma Design
- primary target for PPT table and right-side description/table regions

Structure:

```json
{
  "type": "EDITABLE_TABLE",
  "columns": [{"index": 1, "width": 120, "x": 0}],
  "rows": [{"index": 1, "height": 24, "y": 0}],
  "cells": [
    {
      "row": 1,
      "column": 1,
      "rowSpan": 1,
      "colSpan": 1,
      "bounds": {"x": 0, "y": 0, "width": 120, "height": 24},
      "text": {
        "characters": "Cell text",
        "style": {},
        "fills": [],
        "textRuns": []
      }
    }
  ]
}
```

Rendering rule:

- renderer creates table, row, and cell frames plus native text nodes
- do not render table text as SVG
- table must live under `editable/content` when the layered model is enabled

### VECTOR

필수:

- `fillGeometry`
- `strokeGeometry`
- `fills`
- `strokes`
- `strokeWeight`

용도:

- connector fallback
- diamond / ellipse / bracket
- 복잡 도형

## assets 규칙

이미지 자산은 `assets` 맵으로 연결한다.

구조:

```json
{
  "imageRef": {
    "filename": "optional.png",
    "mime_type": "image/png",
    "base64": "..."
  }
}
```

이미지 node는 `RECTANGLE` + `IMAGE fill` 방식 사용:

```json
{
  "type": "RECTANGLE",
  "fills": [
    {
      "type": "IMAGE",
      "imageRef": "some-ref",
      "scaleMode": "FILL"
    }
  ]
}
```

## 샘플 3페이지 기준 최소 타입 분포

고급 플러그인 JSON 기준 주요 타입은 아래다.

- Page 1
  - `FRAME 3`
  - `GROUP 47`
  - `VECTOR 233`
  - `TEXT 101`
- Page 2
  - `FRAME 2`
  - `GROUP 25`
  - `VECTOR 114`
  - `TEXT 257`
- Page 3
  - `FRAME 12`
  - `GROUP 86`
  - `VECTOR 183`
  - `TEXT 222`
  - `RECTANGLE 11`

해석:

- `VECTOR`와 `TEXT`가 핵심
- `FRAME/GROUP`은 구조 유지용
- `RECTANGLE`은 주로 이미지/박스용

즉 generator는 처음부터 이 타입 분포에 가까운 구조를 목표로 해야 한다.

## PPT candidate -> target node 매핑

### `text_block`

- target: `TEXT`

필수 생성:

- `characters`
- `fills`
- `style`
- `absoluteBoundingBox`

### `shape`

- target: `RECTANGLE` 또는 `VECTOR`

분기:

- 단순 box류: `RECTANGLE`
- diamond / ellipse / bracket / 특수 도형: `VECTOR`

### `labeled_shape`

- target:
  - `FRAME + TEXT`
  - 또는 `GROUP(VECTOR + TEXT)`

분기:

- diamond류: `GROUP(VECTOR + TEXT)`
- box류: `FRAME(TEXT child)`

### `connector`

- target: `VECTOR`

원칙:

- old candidate line renderer를 재사용하지 않음
- generator가 `strokeGeometry` path를 직접 만들어야 함

### `table`

- target: `FRAME`

구조:

- `table FRAME`
- `row FRAME`
- `cell FRAME`
- `TEXT child`

### `image`

- target: `RECTANGLE(image fill)`

### `group`, `section_block`

- target: `GROUP` 또는 `FRAME`

분기:

- 시각 shell 필요 없음: `GROUP`
- fill/stroke가 필요한 묶음: `FRAME`

## fallback 규칙

### connector fallback

초기 단계는 perfect route보다 아래를 우선한다.

- `straightConnector1`
- `bentConnector2`
- `bentConnector4`

생성 규칙:

- `strokeGeometry`는 최소 1개 path 생성
- arrow head가 있으면 `fillGeometry` 또는 별도 마지막 path 추가

### shape fallback

다음은 `VECTOR`로 우선 생성한다.

- decision diamond
- ellipse
- right bracket
- geometry가 애매한 특수 shape

### overlay / clip fallback

generator는 overlay 후보를 metadata로 남긴다.

`debug` 권장 필드:

- `full_page_overlay_candidate`
- `source_path`
- `source_node_id`
- `source_subtype`

renderer는 이 metadata를 기반으로 최종 skip 여부를 판단한다.

## generator 구현 순서

### Phase 1

- page/frame tree
- text
- shape
- image

### Phase 2

- labeled shape
- group/section
- table

### Phase 3

- connector/vector fallback
- overlay/clip metadata

## 중간 산출물

generator 구현 중간 검증을 위해 아래 산출물을 만든다.

1. `pptx-generated-page-1.replay.json`
2. `pptx-generated-page-2.replay.json`
3. `pptx-generated-page-3.replay.json`

검증 방식:

- 렌더 전 JSON 구조 검토
- 같은 renderer에 넣어서 고급 플러그인 bundle과 비교

## 성공 기준

1. generator가 최소 replay target schema를 충족한다
2. renderer에서 에러 없이 렌더된다
3. 샘플 3페이지에서 고급 플러그인 결과와 비교 가능한 수준의 구조가 나온다
4. 이후 보강이 페이지별 땜질이 아니라 타입별 보강으로 이어진다

## 한 줄 결론

이 문서의 역할은 명확하다.

> `고급 플러그인 JSON`은 replay 입력 예시이자 목표 스키마이고,
> 앞으로의 개발은 `PPT -> 이 스키마 생성기`를 만드는 작업이다.
