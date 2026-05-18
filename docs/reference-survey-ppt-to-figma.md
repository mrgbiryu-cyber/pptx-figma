# PPT to Figma 참고 자료 조사

## 1. 전체 요약

현재 바로 참고할 자료는 아래 4부류로 나뉜다.

- 직접 구현 참고용 오픈소스
- Figma 공식 구현 제약 자료
- PPTX 구조 이해 및 fixture 생성용 라이브러리
- 품질 벤치마크 및 QA 파이프라인 참고 자료

핵심 결론은 아래와 같다.

1. 완성형 오픈소스 정답은 없다.
- 따라서 `오픈소스 구현 조각 + 공식 API + 상용 벤치마크` 조합으로 가야 한다.

2. 기술 난점은 parser보다 editable fidelity다.
- 특히 text, font, mixed style, table/cell, group hierarchy 보존이 핵심이다.

3. 품질 판단은 단순 이미지 유사도가 아니다.
- `visual fidelity + structural fidelity + editability + operational mapping` 4축 이상으로 평가해야 한다.

4. 우리 프로젝트는 단순 import 플러그인이 아니라 운영 플랫폼이다.
- 따라서 import 성공보다도 `source_mapping`, `stable identity`, `annotation/ownership/search` 연결 가능성이 더 중요하다.

## 2. 자료별 분석표

| 자료 | 역할 | 우리 프로젝트에 유효한 이유 | 직접 재사용 가능성 | 리스크 | 우선 조사 순위 |
|---|---|---|---|---|---|
| `iori73/pptx-to-design` | PPTX -> Figma import 구현 뼈대 | parser / converter / plugin entry 분리가 명확하고 목표가 가장 유사 | 높음 | 운영 플랫폼 요소 없음 | 1 |
| Figma Plugin API 공식 문서 | 실제 API 제약 확인 | editable fidelity의 핵심 제약 정의 | 매우 높음 | 변환 로직 자체는 제공 안 함 | 1 |
| `figma/plugin-samples` | 플러그인 구조 참고 | 안전한 bootstrap, manifest, UI-message 구조 참고 가능 | 높음 | 변환기 자체 예시는 아님 | 2 |
| `PptxGenJS` | synthetic fixture 생성 | 테스트용 PPT 자동 생성에 적합 | 중간 | parser 코어로는 부적합 | 3 |
| `pptx-automizer` | OOXML 구조 실무 참고 | rels, template, slide reference 이해에 도움 | 중간 | import 코어로 보기 어렵다 | 4 |
| `pptx2img` | PPT 렌더 기반 QA | 원본 vs 결과 snapshot 비교 파이프라인에 유용 | 높음 | editable 품질은 평가 못함 | 5 |
| `office.to.design` | 상용 벤치마크 | 목표 품질선 정의에 적합 | 낮음 | 구현 비공개 | 벤치마크 |
| Pitchdeck PPTX import | 상용 UX 참고 | 사용자 기대 UX와 import 결과 참고 가능 | 낮음 | 절대 기준으로 보기 어려움 | 벤치마크 |

## 3. 자료별 분석

### 3-1. `iori73/pptx-to-design`

저장소:

- <https://github.com/iori73/pptx-to-design>

왜 중요한가:

- 현재 조사 자료 중 우리 목적과 가장 직접적으로 맞닿아 있다.
- PowerPoint(.pptx)를 editable Figma import로 가져오는 목표를 명시한다.
- parser / converter / plugin entry 구조가 분리되어 있어 설계 참고 가치가 높다.

확인한 구조:

- `src/code.ts`
- `src/parser/pptxParser.ts`
- `src/converters/textConverter.ts`
- `src/converters/tableConverter.ts`
- `src/converters/groupConverter.ts`

아키텍처 요약:

1. `code.ts`가 entry point 역할을 한다.
2. `parsePPTX()`로 PPTX ZIP을 읽고 presentation, rels, theme, media, slide XML을 수집한다.
3. slide element를 type별로 converter에 넘겨 Figma node를 만든다.

실제 지원 방향:

- text
- shape
- image
- table
- chart placeholder/image
- group hierarchy

좋은 점:

- PPTX ZIP -> XML/rels/theme/media -> slide -> element dispatch 흐름이 명확하다.
- text range style 처리 아이디어를 얻을 수 있다.
- table를 frame/cell/text로 쪼개는 방식이 보인다.

한계:

- 운영 플랫폼용 canonical model이 없다.
- source mapping이 없다.
- field-level authoritative source 개념이 없다.
- incremental pull, remap, diff를 고려하지 않는다.
- group 중심 출력이 많아 이후 auto-layout/component 승격에 불리할 수 있다.

우리 프로젝트에 재사용 가능한 부분:

- PPTX parser 흐름
- slide / element dispatch 구조
- text conversion 방식
- table를 node 단위로 나누는 기본 아이디어

폐기 또는 대체해야 할 부분:

- import-only 사고방식
- group 중심 출력 전략
- source lineage 없는 구조
- 운영 메타데이터와 무관한 단발성 플러그인 흐름

### 3-2. Figma Plugin API 공식 문서

중요 문서:

- <https://developers.figma.com/docs/plugins/>
- <https://developers.figma.com/docs/plugins/working-with-text/>
- <https://developers.figma.com/docs/plugins/api/TextNode/>
- <https://developers.figma.com/docs/plugins/api/properties/figma-createtext/>
- <https://developers.figma.com/docs/plugins/api/properties/figma-loadfontasync/>
- <https://developers.figma.com/docs/plugins/api/properties/figma-createcomponentfromnode/>

왜 중요한가:

- editable import의 품질은 결국 Figma API 제약 안에서 결정된다.
- 특히 TextNode 제약이 변환 품질에 직접 영향을 준다.

반드시 고려해야 하는 제약:

- 텍스트 속성 변경 전 폰트 로딩 필요
- mixed style 처리 시 range 단위 접근 필요
- missing font 처리 필요
- group과 frame은 운영성이 다르다
- component 생성은 제한이 있어 무조건 자동 승격하면 안 된다
- 대량 node 생성은 성능 이슈 가능성이 있다

우리 프로젝트에 유효한 이유:

- 고품질 변환의 핵심 난점이 text와 node structure이기 때문이다.
- 플러그인 구현보다 API 제약 이해가 우선이다.

### 3-3. `figma/plugin-samples`

저장소:

- <https://github.com/figma/plugin-samples>

왜 중요한가:

- 변환기 자체 예시는 아니지만, plugin bootstrap과 구조를 가장 안전하게 참고할 수 있다.

유효한 포인트:

- manifest 구성
- UI <-> main code message passing
- long-running task 처리 패턴
- scene node 생성 패턴

### 3-4. `PptxGenJS`

저장소:

- <https://github.com/gitbrent/PptxGenJS>

왜 참고하나:

- 기존 PPT를 읽는 parser는 아니지만, 테스트용 PPT fixture를 자동 생성하는 데 유용하다.

권장 용도:

- 표 중심 샘플 생성
- 텍스트 스타일 샘플 생성
- 반복 UI regression fixture 생성

판단:

- import 코어보다는 test input generator로 보는 것이 맞다.

### 3-5. `pptx-automizer`

저장소:

- <https://github.com/singerla/pptx-automizer>

왜 참고하나:

- 기존 PPTX 구조를 다루는 방식, rels, template, slide reference를 실무적으로 이해하는 데 도움이 된다.

권장 용도:

- OOXML 관계 이해
- 템플릿 기반 fixture 가공
- lineage / rels 구조 이해

판단:

- import 코어보다는 PPTX 실무 참고용 보조재다.

### 3-6. `pptx2img`

저장소:

- <https://github.com/captainnarwal/pptx2img>

왜 중요한가:

- editable import 자체를 구현하지는 않지만, 품질 QA 파이프라인에 매우 유용하다.

권장 용도:

- 원본 PPT 렌더 이미지 생성
- slide별 visual regression snapshot 생성
- 레이아웃 붕괴 탐지

### 3-7. `office.to.design`

자료:

- <https://anything.to.design/blog/from-powerpoint-to-figma-slides-the-ultimate-guide/>

왜 중요한가:

- PowerPoint 문서를 editable Figma layer로 가져온다는 점에서 가장 직접적인 상용 벤치마크다.

벤치마크 항목:

- editable text 유지율
- 그룹/계층 유지율
- 표/셀 유지율
- layout fidelity
- import 후 수동 수정 필요량
- component 친화성

판단:

- 구현 참고가 아니라 품질 목표선 참고용이다.

### 3-8. Pitchdeck

자료:

- <https://docs.hypermatic.com/pitchdeck/design/import-pptx>

왜 중요한가:

- 사용자 기대 UX와 import 흐름을 참고하기 좋다.

판단:

- 가능성 증명과 UX 참고에는 유효하지만 절대 품질 기준으로 삼기는 어렵다.

## 4. 기술적 시사점

### 4-1. parser보다 editable fidelity가 핵심이다

PPTX를 읽는 것 자체보다, 읽은 결과를 Figma에서 살아있는 편집 구조로 만드는 것이 더 어렵다.

특히 아래가 핵심 난점이다.

- text
- font loading
- mixed style
- table/cell preservation
- group hierarchy preservation

### 4-2. `group-first`보다 `frame-first` 전략이 유리하다

운영 플랫폼 관점에서 Figma 결과물은 단순 시각 복제가 아니라 이후 관리 가능한 구조여야 한다.

따라서:

- annotation target
- ownership target
- search target
- source mapping
- stable identity
- component 후보화

를 생각하면 `group` 중심보다 `frame` 중심 구조가 유리하다.

### 4-3. table/cell을 node 단위로 남겨야 한다

표가 보이게만 만들면 안 된다.

이후 다음 기능에 쓰이려면 cell 수준 구조가 살아야 한다.

- 담당자 연결
- 정책 연결
- 검색
- diff
- remap

### 4-4. 상용 벤치마크는 “수동 보정 필요량”을 봐야 한다

상용 사례를 볼 때 단순히 “된다/안 된다”보다 아래를 봐야 한다.

- editable text 유지율
- 레이어 구조 이해 가능성
- 표/셀 유지
- import 후 사람이 얼마나 손을 봐야 하는가

## 5. 우리 프로젝트 적용 방안

### 5-1. 바로 가져올 것

- `iori73/pptx-to-design`의 parser 흐름
- Figma 공식 문서의 text handling 규칙
- `pptx2img`를 이용한 snapshot QA 아이디어
- `PptxGenJS`를 이용한 synthetic fixture 생성 전략

### 5-2. 우리 쪽에서 새로 설계할 것

- canonical intermediate model
- `document/page/node/asset/source_mapping`
- frame-first node generation
- field-level source tracking
- stable identity 전략
- 운영 적합성 평가 구조

### 5-3. 품질 평가 파이프라인 초안

1. 원본 PPT를 렌더 이미지로 추출한다.
2. PPT parser 결과로 canonical graph를 생성한다.
3. Figma import를 수행한다.
4. Figma 결과를 다시 이미지로 렌더한다.
5. 시각 비교를 수행한다.
6. 구조 비교를 수행한다.
7. editable 상태를 비교한다.
8. 운영 적합성을 평가한다.

평가 축:

- visual fidelity
- structural fidelity
- editability
- operational mapping

### 5-4. 자동검증 방향

자동검증은 아래를 우선 본다.

- page 매칭
- editable text 비율
- table/cell 구조 유지 여부
- source_mapping 생성률
- node hierarchy 붕괴 여부
- 운영 연결 가능한 node 비율

자동 Fail 후보:

- page 누락
- 텍스트 대부분 비편집
- table 문서가 이미지 수준으로 붕괴
- mapping/identity 생성 실패

### 5-5. 사람검증 방향

사람은 한 가지를 판단하면 된다.

"이 결과물로 Figma에서 실제 기획 작업을 이어갈 수 있는가?"

권장 결과값:

- `usable`
- `usable_with_fix`
- `not_usable`

## 6. 즉시 실행할 조사 TODO

### 6-1. `iori73/pptx-to-design` 심층 분석

다음 파일을 추가로 확인한다.

- `slideParser.ts`
- `shapeConverter.ts`
- `imageConverter.ts`
- `types` 계층

산출물:

- 재사용 가능
- 폐기 대상
- 보완 필요

3분류 문서

### 6-2. Figma text import spec 작성

정리 대상:

- font loading 정책
- missing font fallback
- mixed style 처리 규칙
- large text batch 처리 방식

### 6-3. node generation spec 작성

정리 대상:

- frame-first 정책
- group 사용 제한 규칙
- component 후보 승격 조건
- auto-layout 적용 조건

### 6-4. 품질검증 PoC 설계

정리 대상:

- 샘플 PPT 10~20개 분류
- 자동검증 지표 1차 세트
- 시각 QA 파이프라인
- 사람검증 체크 방식

### 6-5. fixture 전략 수립

정리 대상:

- `PptxGenJS` 기반 synthetic PPT 생성
- 표/셀/그룹/혼합 텍스트/반복 UI 케이스 확보

## 7. 현재 바로 가져다 볼 저장소 TOP 5

1. <https://github.com/iori73/pptx-to-design>
2. <https://developers.figma.com/docs/plugins/>
3. <https://github.com/figma/plugin-samples>
4. <https://github.com/gitbrent/PptxGenJS>
5. <https://github.com/singerla/pptx-automizer>

## 8. 출처

- <https://github.com/iori73/pptx-to-design>
- <https://raw.githubusercontent.com/iori73/pptx-to-design/main/src/code.ts>
- <https://raw.githubusercontent.com/iori73/pptx-to-design/main/src/parser/pptxParser.ts>
- <https://raw.githubusercontent.com/iori73/pptx-to-design/main/src/converters/textConverter.ts>
- <https://raw.githubusercontent.com/iori73/pptx-to-design/main/src/converters/tableConverter.ts>
- <https://raw.githubusercontent.com/iori73/pptx-to-design/main/src/converters/groupConverter.ts>
- <https://developers.figma.com/docs/plugins/>
- <https://developers.figma.com/docs/plugins/working-with-text/>
- <https://developers.figma.com/docs/plugins/api/TextNode/>
- <https://developers.figma.com/docs/plugins/api/properties/figma-createtext/>
- <https://developers.figma.com/docs/plugins/api/properties/figma-loadfontasync/>
- <https://developers.figma.com/docs/plugins/api/properties/figma-createcomponentfromnode/>
- <https://developers.figma.com/docs/plugins/async-tasks/>
- <https://github.com/figma/plugin-samples>
- <https://github.com/gitbrent/PptxGenJS>
- <https://github.com/singerla/pptx-automizer>
- <https://github.com/captainnarwal/pptx2img>
- <https://anything.to.design/blog/from-powerpoint-to-figma-slides-the-ultimate-guide/>
- <https://docs.hypermatic.com/pitchdeck/design/import-pptx>
