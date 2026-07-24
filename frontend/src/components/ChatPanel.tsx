import { useEffect, useRef, useState } from "react";

export interface ChatMessage {
  role: "user" | "assistant" | "status";
  text: string;
  options?: Array<{ label: string; detail?: string; value?: string; action?: string }>;
}

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
  onAction?: (action: string) => void;
  draftSeed?: { value: string; key: number } | null;
}

const SAMPLES = [
  // 조례 데이터가 등록되어 있고 VWorld 주소 검색도 확인한 비도시지역 예시다.
  "충청남도 아산시 음봉면 신수리 100에 창고 지을 수 있어?",
  "충청남도 예산군 삽교읍 두리 100에 공장 지을 수 있어?",
  "인천광역시 계양구 작전동 100에 업무시설 지을 수 있어?",
  "인천광역시 강화군 길상면 온수리 100에 단독주택 지을 수 있어?",
  "대구광역시 군위군 군위읍 동부리 100에 근린생활시설 지을 수 있어?",
  "여기 용적률 250%로 올리면 몇 층이야?",
];

export function ChatPanel({ messages, busy, onSend, onAction, draftSeed }: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!draftSeed) return;
    setDraft(draftSeed.value);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [draftSeed?.key]);

  function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    onSend(trimmed);
    setDraft("");
  }

  return (
    <div className="chat-panel">
      <header className="chat-header">
        <h1>인허가 사전진단</h1>
        <p>공간정보 · 법령 규제 기반 건축 가능성 검토</p>
      </header>

      <div className="chat-log">
        {messages.length === 0 && (
          <div className="samples">
            <p className="samples-title">이렇게 물어보세요</p>
            {SAMPLES.map((s) => (
              <button key={s} className="sample" onClick={() => submit(s)}>
                {s}
              </button>
            ))}
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`bubble bubble-${m.role}`}>
            {m.text}
            {m.options && (
              <div className="address-options">
                {m.options.map((option) => (
                  <button
                    key={`${option.label}-${option.value ?? option.action}`}
                    onClick={() => {
                      if (option.action) onAction?.(option.action);
                      else if (option.value) submit(option.value);
                    }}
                  >
                    <span>{option.label}</span>
                    {option.detail && <small>{option.detail}</small>}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {/* 결과의 성격을 답변을 읽기 전에 알린다. 답변 끝에만 붙이면
          수치를 확정치로 읽은 뒤에야 단서를 만나게 된다. */}
      <p className="disclaimer">
        본 결과는 건축물 용도 대분류 기준 사전 참고용 이론값입니다. 세부 용도
        판정과 실제 인허가 가능 여부는 관할 지자체 조례·개별 법령 확인이
        필요합니다.
      </p>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          submit(draft);
        }}
      >
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="주소와 지으려는 용도를 알려주세요"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          {busy ? "검토 중" : "질의"}
        </button>
      </form>
    </div>
  );
}
