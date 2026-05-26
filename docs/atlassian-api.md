# Atlassian / Confluence API (구현 참조)

이 문서는 `Atlassian API 명세서`를 RAG 파이프라인 구현 관점에서 정리한 참조 문서다.
백엔드(BFF)가 아직 구축되지 않아, PoC에서는 **ML 파이프라인(본 저장소)이 Atlassian REST API를
`atlassian-python-api` 라이브러리로 직접 호출**한다. OAuth 인증·토큰 관리는 Authorization Server
(Spring Security OAuth2 Client)가 담당하며, 본 저장소는 발급된 `access_token`을 전달받아 사용한다.

---

## 책임 경계

| 영역 | 담당 | API |
|---|---|---|
| 인증 (OAuth 2.0 3LO) | Authorization Server (Spring) | `AUTH-01~04` — 로그인 리다이렉트 / 토큰 교환 / 토큰 갱신 / accessible-resources |
| 데이터 수집 | **ML 파이프라인 (본 저장소)** | `DATA-01~03` — 페이지 목록 / CQL 검색 / Space 목록 |

본 저장소는 `access_token` + `cloudid`를 입력으로 받아 데이터 수집 API를 호출한다.
모든 데이터 수집 요청에 `Authorization: Bearer {access_token}` 헤더가 필요하다.

## 데이터 수집 API

API URL 형식: `https://api.atlassian.com/ex/confluence/{cloudid}/rest/api/...`
(`cloudid`는 `AUTH-04 accessible-resources` 응답의 `id`)

### DATA-01. 페이지 목록 조회 (Full Crawl)

`GET /content?type=page&spaceKey={key}&start={n}&limit={≤100}&expand=space,version,body.storage`

서비스 최초 구동 시 전체 문서를 Vector DB에 적재하기 위해 사용. `limit` 최대 100,
초과 시 `start`를 증가시키며 반복 호출(또는 `get_all_pages_from_space_as_generator()`).

### DATA-02. CQL 검색 (Delta Sync)

`GET /content/search?cql={query}&limit={≤100}&expand=body.storage,version,space`

1시간 주기 델타 싱크에 사용. 예: `space="ENG" AND type=page AND lastModified >= "2026-04-29 00:00"`.
`_links.next`가 있으면 커서 기반 다음 페이지 존재.

### DATA-03. Space 목록 조회 (사용자 권한 필터링)

`GET /space?start={n}&limit={≤500}`

로그인 사용자가 **접근 가능한 Space만** 반환된다(Confluence 권한 자동 적용).
RAG 검색 시 ACL 필터의 1차 키로 사용 — **ACL 미해결 사항 참조**.

## 페이지 객체 → PageObject 매핑

`DATA-01/02` 응답의 페이지 객체는 `samples/confluence_sample_data.json`과 동일한 형식이다.

| Atlassian 필드 | PageObject 필드 | 비고 |
|---|---|---|
| `id` | `page_id` | Qdrant 문서 식별자 |
| `title` | `title` | |
| `body.storage.value` | `body_html` | HTML — BeautifulSoup 파싱 필요 |
| `version.number` | `version_number` | 멱등성 검사 |
| `version.when` | `last_modified` | ISO 8601 |
| `space.key` | `space_key` | |
| `metadata.labels.results[].name` | `labels[]` | |
| `ancestors[].{id,title}` | `ancestors[]` | |
| `_links.webui` | `webui_link` | 출처 카드 원본 링크 |
| `attachments[]` | `attachments[]` | 샘플 데이터에 메타만 존재 — 실제 다운로드/추출은 별도 |
| (없음) | `allowed_groups[]` / `allowed_users[]` | **출처 불명 — ACL 미해결 사항 참조** |

## ⚠ ACL 미해결 사항

설계서·기획서 §6.6은 ACL을 청크별 `allowed_groups`/`allowed_users` Payload로 정의하지만,
**제공된 Atlassian API 명세에는 페이지 단위 권한(content restrictions) API가 없다.**
명세상 권한 정보는 `DATA-03`(사용자가 접근 가능한 Space 목록)뿐이며, 기획서 §6.2/§6.5도
Authorization Server가 수집하는 ACL을 "스페이스 접근 권한"으로 기술한다. 샘플 데이터에도 ACL 필드가 없다.

→ PoC ACL Pre-filtering을 (a) `space_key` 기반(사용자 접근 가능 스페이스)으로 할지,
(b) Confluence content restrictions API를 추가 도입해 `allowed_groups`/`allowed_users`를 채울지
**팀 결정이 필요하다.** `docs/db-schema.md`·`docs/ai/current-plan.md`의 선행 의존성 참조.

## Cloud ↔ Server/DC 전환

URL 3개(인증 요청 / 토큰 교환 / API 호출)만 환경 변수로 관리하면 코드 수정 없이 전환 가능
(`app/config.py`에서 관리).

## 공식 문서

- OAuth 2.0 3LO: https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
- REST API v1: https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/
- CQL: https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/
