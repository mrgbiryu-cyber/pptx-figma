# `iori73/pptx-to-design` 재사용 / 폐기 / 보완 초안

## 재사용할 것

- PPTX ZIP 파싱의 기본 흐름
- `presentation.xml`, rels, theme, media, slide XML 해석 순서
- slide element dispatch 구조
- text converter의 range style 적용 아이디어
- table를 frame/cell/text로 나누는 기본 방식

## 폐기할 것

- import-only 관점의 단발성 흐름
- 운영 메타데이터 없는 결과 구조
- source mapping 없는 결과 저장 방식
- group 중심 최종 출력 전략

## 보완할 것

- frame-first node generation 정책
- canonical intermediate model 추가
- `document/page/node/asset/source_mapping` 연결
- stable identity 정책
- text font fallback 정책
- mixed style 처리 강화
- table/cell 운영 적합성 평가
- annotation/ownership/search target 보장
- 이후 incremental pull을 고려한 mapping 저장

## 다음 조사 포인트

- `slideParser.ts`가 실제로 어떤 level까지 구조를 분해하는지
- `shapeConverter.ts`가 어떤 shape를 단순화하는지
- `imageConverter.ts`가 asset 분리를 어떻게 처리하는지
- chart 처리 수준이 placeholder인지 실제 구조인지
- group 생성이 frame보다 group에 치우치는지
