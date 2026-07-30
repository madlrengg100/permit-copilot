import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

/** 인라인 서식: **볼드**, `코드`, [링크](https://...). */
function renderInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)]+\))/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
    if (link) {
      return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    }
    return <span key={i}>{part}</span>;
  });
}

/**
 * 답변 텍스트의 마크다운을 렌더링한다(라이브러리 없이 최소 구현).
 * 지원: 제목(#), 불릿(- / *), 번호목록, 인용(>), 구분선(---), 인라인 볼드·코드.
 */
function renderMarkdownCore(text: string): ReactNode {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let list: ReactNode[] = [];
  let ordered = false;
  let orderedStart = 1;

  const flush = () => {
    if (!list.length) return;
    const items = list;
    blocks.push(
      ordered ? (
        <ol key={`l${blocks.length}`} className="md-list" start={orderedStart}>{items}</ol>
      ) : (
        <ul key={`l${blocks.length}`} className="md-list">{items}</ul>
      ),
    );
    list = [];
  };

  lines.forEach((raw, i) => {
    const t = raw.trim();
    const bullet = t.match(/^[-*]\s+(.*)/);
    const numbered = t.match(/^(\d+)\.\s+(.*)/);
    if (bullet) {
      if (list.length && ordered) flush();
      ordered = false;
      list.push(<li key={i}>{renderInline(bullet[1])}</li>);
      return;
    }
    if (numbered) {
      if (list.length && !ordered) flush();
      if (!list.length) orderedStart = Number(numbered[1]);
      ordered = true;
      list.push(<li key={i}>{renderInline(numbered[2])}</li>);
      return;
    }
    flush();
    if (!t) return; // 빈 줄
    const heading = t.match(/^(#{1,4})\s+(.*)/);
    if (heading) {
      blocks.push(<div key={i} className="md-h">{renderInline(heading[2])}</div>);
      return;
    }
    if (/^-{3,}$/.test(t)) {
      blocks.push(<hr key={i} className="md-hr" />);
      return;
    }
    if (t.startsWith(">")) {
      blocks.push(<blockquote key={i} className="md-quote">{renderInline(t.replace(/^>\s?/, ""))}</blockquote>);
      return;
    }
    blocks.push(<p key={i} className="md-p">{renderInline(t)}</p>);
  });
  flush();
  return blocks;
}

// 접기/펼치기로 만들 섹션 규칙. 헤더 제목이 test 에 맞으면 그 섹션을 <details>로 감싼다.
const COLLAPSIBLE_SECTIONS: Array<{ test: RegExp; label: string }> = [
  { test: /국가법령정보센터 원문 확인/, label: "국가법령정보센터 원문" },
  { test: /관련 법령 조문\(근거\)/, label: "인허가 단계 관련 법령 조문" },
  { test: /관련 조례 조문\(근거\)/, label: "지자체 관련 조례 조문" },
];

function renderMarkdown(text: string): ReactNode {
  const lines = text.split("\n");
  // 상단 섹션 헤더(## N. 제목) 위치들
  const headers: number[] = [];
  lines.forEach((line, i) => {
    if (/^#{1,4}\s+/.test(line.trim())) headers.push(i);
  });
  if (headers.length === 0) return renderMarkdownCore(text);

  const nodes: ReactNode[] = [];
  if (headers[0] > 0) {
    nodes.push(
      <Fragment key="pre">{renderMarkdownCore(lines.slice(0, headers[0]).join("\n"))}</Fragment>,
    );
  }
  for (let h = 0; h < headers.length; h += 1) {
    const startH = headers[h];
    const endH = h + 1 < headers.length ? headers[h + 1] : lines.length;
    const headerLine = lines[startH].trim();
    const rule = COLLAPSIBLE_SECTIONS.find((r) => r.test.test(headerLine));
    if (rule) {
      const bodyLines = lines.slice(startH + 1, endH).filter((line) => line.trim());
      const count = bodyLines.filter((line) => /^\s*-\s+/.test(line)).length;
      nodes.push(
        <details className="law-sources" key={startH}>
          <summary>
            <span className="law-summary-closed">▾ {rule.label} {count}건 펼치기</span>
            <span className="law-summary-open">▴ {rule.label} {count}건 닫기</span>
          </summary>
          <div className="law-sources-body">{renderMarkdownCore(bodyLines.join("\n"))}</div>
        </details>,
      );
    } else {
      nodes.push(
        <Fragment key={startH}>{renderMarkdownCore(lines.slice(startH, endH).join("\n"))}</Fragment>,
      );
    }
  }
  return <>{nodes}</>;
}

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
  "인천광역시 계양구 작전동 100에 상업시설 지을 수 있어?",
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
            {m.role === "assistant" ? renderMarkdown(m.text) : m.text}
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
        건축물 용도 대분류 기준 사전 참고값입니다.
        <br />
        실제 인허가는 관할 조례·개별 법령 확인이 필요합니다.
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
