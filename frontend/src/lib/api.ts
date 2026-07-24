// 백엔드는 vite dev 서버가 /api 로 프록시한다(vite.config.ts).
// 덕분에 접속 경로(터널/외부IP/로컬)와 무관하게 같은 주소를 쓰고,
// 열어야 할 포트도 프론트 하나뿐이다.
const BASE = import.meta.env.VITE_API_BASE ?? "";

// 백엔드 APP_TOKEN 과 일치해야 /api/chat 이 열린다.
const APP_TOKEN = import.meta.env.VITE_APP_TOKEN ?? "";

export interface SSEEvent {
  event: string;
  data: any;
}

export interface AddressSuggestion {
  title: string;
  road: string;
  parcel: string;
  address: string;
  lon: number;
  lat: number;
}

export async function searchAddresses(query: string): Promise<AddressSuggestion[]> {
  const res = await fetch(`${BASE}/api/address-search?q=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error(`주소 후보 조회 오류 ${res.status}`);
  const data = await res.json();
  return Array.isArray(data.items) ? data.items : [];
}

export async function fetchConfig(): Promise<{ vworld_key: string; mock_mode: boolean }> {
  const res = await fetch(`${BASE}/api/config`);
  return res.json();
}

/** 질의를 보내고 SSE 이벤트를 순서대로 뱉는 async generator. */
export async function* streamChat(
  sessionId: string,
  message: string,
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(APP_TOKEN ? { "X-App-Token": APP_TOKEN } : {}),
    },
    body: JSON.stringify({ session_id: sessionId, message }),
  });

  if (res.status === 401) {
    throw new Error("앱 토큰이 올바르지 않습니다. VITE_APP_TOKEN 과 백엔드 APP_TOKEN 을 맞추세요.");
  }
  if (!res.ok) throw new Error(`백엔드 오류 ${res.status}`);
  if (!res.body) throw new Error("응답 스트림이 없습니다.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 는 빈 줄로 이벤트를 구분한다
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      let eventName = "message";
      let dataLine = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLine = line.slice(6);
      }
      if (!dataLine) continue;
      yield { event: eventName, data: JSON.parse(dataLine) };
    }
  }
}
