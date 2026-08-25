import React, { useState } from 'react';
import { Card } from './Card';

export default function Dashboard() {
  const [count, setCount] = useState(0);
  const items = ['예약', '진료시간', '주차'];

  return (
    <div className="min-h-screen bg-gray-100 p-8">
      <h1 className="text-2xl font-bold mb-4">병원 챗봇 프로토타입</h1>
      <p className="text-gray-600 mb-6">한글이 정상 표시되는지 확인용</p>
      <div className="flex gap-2 mb-6">
        {items.map((it) => (
          <span key={it} className="px-3 py-1 bg-white rounded-full shadow text-sm">{it}</span>
        ))}
      </div>
      <button
        onClick={() => setCount(count + 1)}
        className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
        클릭 {count}
      </button>
    </div>
  );
}
