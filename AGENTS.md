# ProofResearch 작업 지침

- 기존 KIC 기획안의 Python·Pydantic·Streamlit·SQLite 구조를 따른다. 현재 상태는 README와 docs/implementation-plan.md에 기록한다.
- 계산은 Decimal을 사용한다. 기준일, 공시, 기간, 통화, 계정이 불명확하면 값을 추측하지 말고 보류한다.
- 합성 예제와 실제 SEC 자료를 모든 화면·내보내기에서 구분한다. PASS를 원문 대조나 독립 평가 완료로 표현하지 않는다.
- 비밀·연락처·.env·원본 제출물·캐시·실행 보고서는 커밋하지 않는다. SEC 식별자는 환경변수로만 받는다.
- 검산 또는 데이터 선택 규칙을 바꾸면 관련 반례 테스트도 추가하고 `uv run pytest`를 실행한다.
- RAG와 LLM을 추가할 때 원문은 지시가 아닌 데이터로 취급하고 기업·공시 필터를 코드에서 고정한다.
