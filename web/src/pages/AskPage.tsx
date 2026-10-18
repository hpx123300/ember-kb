import { useEffect, useRef, useState } from "react";
import { ArrowUp, BookMarked, Square } from "lucide-react";
import { api } from "../lib/api";
import type { AskMeta } from "../lib/types";
import { Button, Card, Typing } from "../components/ui";

interface Turn {
  role: "user" | "assistant";
  content: string;
  meta?: AskMeta;
  error?: boolean;
}

const SUGGESTIONS = [
  "年假有几天，怎么申请？",
  "客户说太贵了怎么回？",
  "住宿标准是多少？",
  "机器人答非所问怎么处理？",
];

export default function AskPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .libStats()
      .then((s) => setCategories(s.categories))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  async function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setTurns((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "" },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    let answer = "";
    try {
      await api.ask(
        question,
        category,
        {
          onToken: (t) => {
            answer += t;
            setTurns((prev) => {
              const next = [...prev];
              next[next.length - 1] = { role: "assistant", content: answer };
              return next;
            });
          },
          onMeta: (meta) => {
            setTurns((prev) => {
              const next = [...prev];
              next[next.length - 1] = { role: "assistant", content: answer, meta };
              return next;
            });
          },
        },
        controller.signal,
      );
    } catch (e) {
      const aborted = (e as Error).name === "AbortError";
      setTurns((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          role: "assistant",
          content: aborted ? answer || "（已停止）" : `出了点问题：${(e as Error).message}`,
          error: !aborted,
        };
        return next;
      });
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto flex flex-col" style={{ minHeight: "calc(100vh - 300px)" }}>
      <div className="flex-1 space-y-6">
        {turns.length === 0 && (
          <div className="animate-rise">
            <div className="panel px-7 py-9">
              <div className="display text-[20px] text-ink leading-snug">
                资料里的答案，
                <br />
                都能点回去看原文。
              </div>
              <p className="text-[13px] text-muted mt-3 max-w-md leading-relaxed">
                我只回答知识库里写过的东西。查不到会直接告诉你，不猜、不补全。
                回答里的每个数字都来自原文，引用可以展开对照。
              </p>
              <div className="flex flex-wrap gap-2 mt-6 stagger">
                {SUGGESTIONS.map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="chip hover:border-accent/40 hover:text-accent transition-colors cursor-pointer"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={i} className="flex justify-end animate-rise">
              <div className="max-w-[80%] rounded-[16px] rounded-br-[6px] bg-accent-soft px-4 py-2.5 text-[13.5px] text-ink leading-relaxed">
                {turn.content}
              </div>
            </div>
          ) : (
            <AssistantTurn key={i} turn={turn} typing={busy && i === turns.length - 1 && !turn.content} />
          ),
        )}
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-4 mt-8">
        {categories.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 mb-2.5 px-1">
            <span className="text-[11px] text-faint mr-1">限定范围</span>
            {["", ...categories].map((c) => (
              <button
                key={c || "all"}
                onClick={() => setCategory(c)}
                className={`rounded-full px-2.5 py-1 text-[11.5px] border transition-colors ${
                  category === c
                    ? "border-accent text-accent bg-accent-soft"
                    : "border-line text-muted hover:text-ink"
                }`}
              >
                {c || "全部"}
              </button>
            ))}
          </div>
        )}
        <div className="panel p-2 pl-4 flex items-end gap-2 shadow-[0_14px_40px_-28px_rgba(80,60,40,0.6)]">
          <input
            className="flex-1 bg-transparent outline-none text-[14px] py-2.5 placeholder:text-faint"
            placeholder="问点什么，比如：年假怎么申请"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) send();
            }}
          />
          {busy ? (
            <Button variant="ghost" onClick={() => abortRef.current?.abort()} className="!px-3">
              <Square size={12} />
              停止
            </Button>
          ) : (
            <Button variant="primary" onClick={() => send()} disabled={!input.trim()} className="!px-3.5">
              <ArrowUp size={14} />
            </Button>
          )}
        </div>
        <div className="text-[10.5px] text-faint mt-2 px-1">
          回答仅依据内部资料，涉及金额与时限以原文为准
        </div>
      </div>
    </div>
  );
}

function AssistantTurn({ turn, typing }: { turn: Turn; typing: boolean }) {
  const refused = turn.meta?.refused;
  return (
    <div className="animate-rise">
      <div className="flex items-center gap-2 mb-2">
        <div className="h-6 w-6 rounded-[8px] bg-accent text-[#fff8f2] grid place-items-center">
          <BookMarked size={12} />
        </div>
        <span className="text-[11.5px] text-faint">薪火</span>
        {turn.meta && !refused && (
          <span className="text-[10.5px] text-faint">
            · 覆盖率 {(turn.meta.coverage * 100).toFixed(0)}%
          </span>
        )}
      </div>

      {typing ? (
        <Typing />
      ) : (
        <div
          className={`rounded-[16px] rounded-tl-[6px] px-5 py-4 text-[13.8px] leading-[1.85] whitespace-pre-wrap ${
            refused
              ? "bg-sage-soft/60 border border-[#d8ddc8] text-ink-soft"
              : turn.error
                ? "bg-[#fbf1ee] border border-[#eccfc7] text-ink-soft"
                : "bg-panel border border-line text-ink"
          }`}
        >
          {turn.content || "…"}
        </div>
      )}

      {turn.meta && turn.meta.citations.length > 0 && (
        <div className="mt-3 space-y-2 stagger">
          <div className="text-[10.5px] text-faint tracking-wide">出处</div>
          {turn.meta.citations.map((c) => (
            <details key={c.index} className="cite-card group cursor-pointer">
              <summary className="list-none flex items-start gap-2.5">
                <span className="font-mono text-[10.5px] text-accent mt-[3px] shrink-0">
                  [{c.index}]
                </span>
                <span className="min-w-0">
                  <span className="text-[12.5px] text-ink block truncate">{c.title}</span>
                  <span className="text-[10.5px] text-faint font-mono block truncate">{c.source}</span>
                </span>
              </summary>
              <div className="mt-2 pl-6 text-[12px] text-muted leading-relaxed border-t border-line-2 pt-2">
                {c.snippet}…
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
