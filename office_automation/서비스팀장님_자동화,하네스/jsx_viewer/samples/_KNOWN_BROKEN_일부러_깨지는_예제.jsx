// ============================================================
//  이 파일은 «일부러» 깨지게 만든 예제입니다. 도구 고장이 아닙니다.
//
//  더블클릭하면 화면이 안 나옵니다. 그게 정상입니다.
//  open-jsx.ps1 이 처리하지 못하는 3가지 패턴을 한 파일에 모아둔 것:
//    1) 여러 줄 import  -> 나머지 줄이 남아 구문 오류
//    2) export default () => ...  -> "export" 만 지워져 'default' 가 남음
//    3) 그래서 컴포넌트명을 못 찾아 존재하지 않는 <App /> 으로 폴백
//
//  용도: 스크립트를 패치했을 때 «고쳐졌는지» 판정하는 회귀 테스트.
//        이 파일이 정상 렌더되면 패치 성공.
//  정상 동작 예제는 ok_dashboard.jsx 를 보세요.
// ============================================================

import React, {
  useState,
  useEffect
} from 'react';

const Badge = ({ text }) => <span className="px-2 py-1 bg-gray-200">{text}</span>;

export default () => {
  const [n, setN] = useState(1);
  return <div className="p-8"><Badge text={"값 " + n} /></div>;
};
