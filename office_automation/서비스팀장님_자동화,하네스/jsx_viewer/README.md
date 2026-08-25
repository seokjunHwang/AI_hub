# jsx_viewer

기획 프로토타입 `.jsx` 를 **npm·빌드 없이 더블클릭만으로** 브라우저에서 보는 스크립트. Windows 전용.
출처: 서비스팀 공유 (`open-jsx.ps1`, 원본 그대로 보관)

---

## 무엇을 하는가

```
.jsx 더블클릭
  └─ 레지스트리 파일연결 → powershell -File open-jsx.ps1 "%1"
       ├─ ① import 줄 제거              React 를 CDN UMD 전역으로 대체
       ├─ ② export default 컴포넌트명 추출   루트로 렌더할 대상 결정
       ├─ ③ export 키워드 제거           모듈 문법을 스크립트 문법으로
       ├─ ④ </script> 이스케이프          블록이 조기 종료되는 것 방지
       └─ ⑤ HTML 템플릿에 삽입 → %TEMP%\jsx-viewer\<이름>.html → 기본 브라우저
```

**빌드 단계를 브라우저로 옮긴 것**이다. Babel standalone 이 런타임에 JSX 를 컴파일하므로 로컬에 툴체인이 필요 없다. 대가는 인터넷 연결(CDN 4개: react, react-dom, babel, tailwind).

### 핵심 트릭 3개

| | 내용 | 왜 |
|---|---|---|
| 훅 미리 풀기 | `const { useState, ... } = React` 를 템플릿에 넣어둠 | 원본 JSX 를 손대지 않아도 `useState` 가 그대로 동작 |
| **Babel `@7` 고정** | `@babel/standalone@7` | 8+ 의 react 프리셋은 automatic runtime 이 기본 → `import` 를 주입해 UMD 방식이 통째로 깨짐 |
| **스크립트 전체 ASCII** | 주석까지 영어 | 파일연결이 **Windows PowerShell 5.1** 로 실행되고, 5.1 은 BOM 없는 UTF-8 을 CP949 로 읽음 |

마지막 항목은 스크립트를 고칠 때 반드시 지켜야 한다. 확인 방법:
```powershell
$b = [IO.File]::ReadAllBytes("open-jsx.ps1"); ($b | Where-Object { $_ -gt 127 }).Count   # 0 이어야 함
```

---

## 설치 / 제거

**이 PC 는 2026-08-25 설치 완료.** 핸들러 경로는 이 리포 안이다.

```
HKCU\Software\Classes\.jsx                       = JsxViewer.File
HKCU\Software\Classes\JsxViewer.File\shell\open\command
  = powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden
    -File "...\office_automation\서비스팀장님_자동화,하네스\jsx_viewer\open-jsx.ps1" "%1"
```

### 무엇을 더블클릭하는가

```
open-jsx.ps1  설치용. 한 번만 실행. 더블클릭으로는 실행되지 않는다
              (Windows 기본값이 .ps1 을 에디터로 열기 때문 — 보안 설정)
*.jsx         이걸 더블클릭한다. 설치 후에는 이것만
```

⚠ **리포를 옮기거나 지우면 파일연결이 깨진다.** 경로가 레지스트리에 박히기 때문이다.
옮긴 뒤 새 위치에서 아래를 한 번 실행하면 갱신된다 (실제로 `tools/` → 이 폴더로 옮길 때 필요했다).

경로에 쉼표·한글·공백이 있어도 동작한다 (`-File "..."` 로 인용되므로). 이 폴더명으로 검증했다.

```powershell
# 설치 / 경로 갱신 (인자 없이 실행하면 스스로 파일연결 등록)
powershell -ExecutionPolicy Bypass -File <경로>\open-jsx.ps1

# 제거
Remove-Item HKCU:\Software\Classes\.jsx -Recurse
Remove-Item HKCU:\Software\Classes\JsxViewer.File -Recurse   # progid 까지 정리
```

`HKCU` 만 쓰므로 관리자 권한이 필요 없고 시스템 전역을 건드리지 않는다.

**설치 후에는 `.jsx` 더블클릭이 에디터가 아니라 브라우저로 간다.** 편집할 때는 에디터에서 직접 열거나 우클릭 → 연결 프로그램.

---

## 실제로 검증한 것 (2026-08-25, PowerShell 5.1.26100)

`samples/` 두 개로 변환 결과를 확인했다. 브라우저 실행은 뺀 사본으로 HTML 생성만 검사.

### ✅ 정상 — `samples/ok_dashboard.jsx`

```
import React, { useState } from 'react';   ← 제거됨
export default function Dashboard() {      ← function Dashboard() 로 변환
render(<Dashboard />)                      ← 루트 정확히 잡음
한글                                        ← 보존됨 (UTF-8 no-BOM + meta charset)
```

### ❌ 깨짐 — `samples/_KNOWN_BROKEN_일부러_깨지는_예제.jsx`

여러 줄 `import` + 화살표 `export default` 를 쓰면 **세 곳이 동시에** 깨진다.

```javascript
// 변환 결과
  useState,              ← 여러 줄 import 의 나머지가 그대로 남음 (구문 오류)
  useEffect
} from 'react';

default () => {          ← "export" 만 지워져 'default' 가 남음 (구문 오류)
...
render(<App />)          ← 컴포넌트명을 못 찾아 App 으로 폴백. App 은 존재하지 않음
```

원인: import 제거 정규식이 `^\s*import\b[^\r\n]*\r?\n` 로 **한 줄만** 매칭하고, default export 탐지가 `function 이름` 과 `이름` 두 형태만 본다.

### 그래서 지켜야 하는 작성 규칙

| 규칙 | 이유 |
|---|---|
| `import` 는 **한 줄로** | 여러 줄이면 나머지가 남아 구문 오류 |
| `export default function 이름()` 형태로 | 화살표·`class`·`memo(...)` 는 인식 못 함 |
| **단일 파일로 자체 완결** | 로컬 컴포넌트 `import` 도 함께 지워져 undefined 가 됨 |
| `.tsx` 는 안 됨 | `data-presets="react"` 만 있고 typescript 프리셋 없음 |

프로토타입 한 화면 = 한 파일 원칙이면 문제가 안 된다. 실제로 그렇게 쓰는 도구다.

---

## 그 외 알아둘 것

- **UserChoice 가 이길 수 있다** — 예전에 `.jsx` 를 VS Code 등으로 «항상 이 앱으로 열기» 지정했다면 `HKCU\...\FileExts\.jsx\UserChoice` 가 우선한다. 설치했는데 여전히 에디터가 열리면 이걸 지워야 한다.
- 오류는 화면 상단 빨간 상자에 표시된다. 단 `window.error` 리스너라서 Babel 컴파일 오류는 콘솔(F12)을 봐야 하는 경우가 있다.
- Tailwind 는 play CDN(v3) 이라 커스텀 config·플러그인이 없다.
- 변환 산출물은 `%TEMP%\jsx-viewer\` 에 남는다. 같은 파일명이면 덮어쓴다.
- macOS 는 미지원.

---

## 파일

```
open-jsx.ps1   원본 스크립트 (ASCII only, BOM 없음 — 수정 시 유지 필수)
samples/
├── ok_dashboard.jsx                      정상 동작 확인용 — 이걸 더블클릭
└── _KNOWN_BROKEN_일부러_깨지는_예제.jsx    회귀 테스트 픽스처. 안 열리는 게 정상
```

깨지는 예제를 남겨둔 이유: 위 «깨짐» 항목을 **추측이 아니라 실행으로 확인**했다는 근거이고,
스크립트를 패치했을 때 **고쳐졌는지 판정하는 기준**이 된다 (이 파일이 렌더되면 패치 성공).
필요 없으면 지워도 도구 동작에는 영향이 없다.
