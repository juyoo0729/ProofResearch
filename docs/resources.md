# 데이터·모델·오픈소스·AI 활용 기록

확인일: 2026-10-12. 의존성 버전은 `uv.lock`, v1 평가 당시의 코드·원문 해시는 `evaluation/freeze.json`과 `evaluation/corpus.json`으로 고정한다. 이후 제품 수정과 구분하며 [v1 보존본에서 재현](../evaluation/README.md#오류-수정-이후-v1-재현)한다.

| 자원 | 사용 범위 | 출처·확인 |
| --- | --- | --- |
| SEC EDGAR 제출 이력·Company Facts | 기업·공시 선택, 표준 계정 수치 | [공식 API 안내](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) |
| SEC 10-K·10-K/A HTML | 연결 손익계산서와 사업 위험 문단 | 각 보고서와 `evaluation/gold.json`의 accession·URL·SHA-256·XPath |
| SEC 접근 정책 | 서비스 식별 헤더, 캐시, 요청 제한 | [Developer Resources](https://www.sec.gov/about/developer-resources) |
| GPT-6 Luna | 계획·도구 선택·정성 설명·기준선 응답 | [공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-luna), 2026-10-11 가격 확인 |
| Streamlit 1.65.0 | 로컬 웹 화면 | 설치 패키지 메타데이터: Apache-2.0, [저장소](https://github.com/streamlit/streamlit) |
| Pydantic 2.14.0 | 입력·출력 스키마 | 설치 패키지 메타데이터: MIT, [저장소](https://github.com/pydantic/pydantic) |
| lxml 6.1.3 | 실행하지 않는 HTML 파싱 | 설치 패키지 메타데이터: BSD-3-Clause, [저장소](https://github.com/lxml/lxml) |
| pytest 9.1.1 | 개발 검사 | 설치 패키지 메타데이터: MIT, [공식 문서](https://docs.pytest.org/) |

연락 이메일은 SEC의 요청 식별 헤더로만 사용하며, API 키는 OpenAI 인증에만 사용한다. 두 값 모두 로컬 환경변수에 저장하며 저장소·보고서·평가 파일에 넣지 않는다. 모델에는 공개 공시에서 추출한 자료를 전달한다. 기업 로고·사진·유료 뉴스는 수집하지 않는다. KIC 로고는 공식 제출 양식 안의 요소로만 유지하며 기관의 채택 또는 보증을 의미하지 않는다.

기획·코드·원문 전사·검산 테스트·평가 검토·제출 문서 작성에 OpenAI Codex를 보조 도구로 사용했다. 금융 수치 정답은 원문 표를 별도로 확인해 전사했고, 평가값은 실제 모델 실행 결과다. 개발 보조 AI가 정답 작성과 서술 검토를 함께 지원했으므로 평가자 독립성은 없다. 참가자는 최종 제출 전 구현 범위·출처·평가 한계와 AI 활용 내역을 확인해야 한다.

텍스트·표 자료의 출처는 [SEC 재사용 안내](https://www.sec.gov/about/webmaster-frequently-asked-questions)와 개별 공시 URL을 함께 기록한다. 이 목록은 사용한 주요 자원의 기록이며, 모든 전이 의존성의 라이선스를 재분류하거나 프로젝트 자체의 배포 라이선스를 새로 부여하는 문서는 아니다.
