# MemoRelay

IPlus Chart 위에 겹쳐 뜨는 위장 입력/응답 칸. 질문을 `claude -p`(구독 로그인)로 보내고 답을 메모처럼 보여줌.

- 입력칸: 기본 탭 증상칸 자리. 비어 있으면 완전히 투명하고 클릭이 통과해서 아래 IPlus 증상칸을 그대로 쓸 수 있음.
  Ctrl+Alt+A 로 커서를 넣으면 반투명, 글자를 치면 불투명. Enter 전송, Shift+Enter 줄바꿈.
- 응답칸: 명세서메모(MX999) 자리, 응답이 있을 때만 보임. 처리 중엔 `...`, 첨부하면 첨부 목록 표시.

## 실행
- `Start-MemoRelay-py.cmd` 더블클릭 (pythonw, 콘솔 없음).
- 종료는 입력칸에 `/quit`.

## 처음 한 번: CLI 로그인
`powershell -ExecutionPolicy Bypass -File .\Login-Claude.ps1` 실행 → 브라우저에서 Claude 구독 계정으로 로그인 (2026-10-28 완료, Max).

## 조작
| 키 / 명령 | 동작 |
|---|---|
| Ctrl+Alt+A | 입력칸에 커서 넣기 |
| Enter / Shift+Enter | 전송 / 줄바꿈 |
| Esc | 진행 중이면 취소 → 입력 지움 → 첨부 비움 → 응답칸 숨김 |
| Ctrl+V | 복사한 파일이나 캡처 이미지를 첨부 |
| Ctrl+O, `/file` | 파일 선택해서 첨부 |
| 파일 첨부 | Ctrl+V(복사한 파일/캡처 이미지) 또는 Ctrl+O / `/file` (드래그앤드롭은 없음) |
| 마우스 휠 | 응답칸 스크롤 |
| `/new` | 새 대화 |
| `/opus` `/sonnet` `/haiku`, `/model <id>` | 모델 변경 (`/model` 만 치면 현재 모델) |
| Ctrl+Alt+Z | 두 칸 즉시 숨기기/보이기 |
| Ctrl+Alt+F12 | 보정 모드 시작/저장 (아래) |

## 보정 모드 (칸 위치 미세조정)
Ctrl+Alt+F12 → 입력칸은 노란색, 응답칸은 분홍색 반투명으로 떠서 아래 IPlus 칸 테두리가 비쳐 보임.
- 맞출 칸을 클릭(그대로 끌면 이동)
- 방향키 1px 이동, Shift+방향키 1px 크기 조절, Ctrl 을 같이 누르면 10px
- Ctrl+Alt+F12 다시 누르면 저장, Esc 는 취소(원래 위치로)

## 파일
- `memo-relay.json` 설정 (좌표, 글꼴, 모델, 단축키)
- `system-prompt.txt` 답변 말투
- `history\` 대화 기록 (평문, 대화별 .md)
- `work\` claude 작업 폴더, 붙여넣은 이미지 저장
- `memo-relay.log` 오류 로그
- `memorelay.py` 본체, `Login-Claude.ps1` CLI 로그인 helper

## exe 로 묶기 (선택)
PowerShell(일반) 에서:

    cd "C:\Users\진료실1-2\Documents\mk\claude\MemoRelay"
    pyinstaller --noconsole --onefile --name MemoRelay memorelay.py

dist\MemoRelay.exe 가 생긴다. exe 는 자기가 놓인 폴더를 기준으로 설정/기록(memo-relay.json, system-prompt.txt, history\)을 찾으니, exe 를 이 폴더에 두거나 그 파일들을 exe 옆에 같이 둔다.
