import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, MessageCircle, X } from "lucide-react";
import { api, newSessionId } from "../lib/api";
import type { Chapter, Mastery, Quiz, QuizResult } from "../lib/types";
import { Button, Card, EmptyState, Loading, SectionTitle, Skeleton, Typing } from "../components/ui";

export default function LearnPage() {
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [mastery, setMastery] = useState<Mastery | null>(null);
  const [active, setActive] = useState<Chapter | null>(null);
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [loadingQuiz, setLoadingQuiz] = useState(false);
  const [answers, setAnswers] = useState<number[]>([]);
  const [result, setResult] = useState<QuizResult | null>(null);

  async function refresh() {
    const [c, m] = await Promise.all([api.chapters(), api.mastery()]);
    setChapters(c);
    setMastery(m);
  }

  useEffect(() => {
    refresh().catch(() => setChapters([]));
  }, []);

  const bestByChapter = useMemo(() => {
    const map: Record<string, { best: number; passed: boolean; attempts: number }> = {};
    mastery?.chapters.forEach((c) => {
      map[c.chapter] = { best: c.best, passed: c.passed, attempts: c.attempts };
    });
    return map;
  }, [mastery]);

  async function startQuiz(chapter: Chapter) {
    setActive(chapter);
    setQuiz(null);
    setResult(null);
    setAnswers([]);
    setLoadingQuiz(true);
    try {
      const data = await api.quiz(chapter.id, 5);
      setQuiz(data);
      setAnswers(new Array(data.questions.length).fill(-1));
    } catch (e) {
      setQuiz(null);
      setResult(null);
      alert((e as Error).message);
    } finally {
      setLoadingQuiz(false);
    }
  }

  async function submit() {
    if (!quiz || !active) return;
    if (answers.some((a) => a < 0)) return;
    const res = await api.submitQuiz(quiz.session_id, answers);
    setResult(res);
    refresh().catch(() => undefined);
  }

  if (chapters === null) {
    return (
      <div className="max-w-4xl">
        <Skeleton rows={4} />
      </div>
    );
  }
  if (chapters.length === 0) {
    return <EmptyState title="还没有培训资料" desc="去「资料」页上传一份 Markdown，章节会自动生成。" />;
  }

  return (
    <div className="max-w-4xl">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <SectionTitle title="章节" desc={`共 ${chapters.length} 章，学完一章就考一次`} />
          <div className="grid sm:grid-cols-2 gap-3 stagger">
            {chapters.map((c) => {
              const record = bestByChapter[c.id];
              return (
                <Card key={c.id} hover className="!p-4 cursor-pointer group" >
                  <div onClick={() => startQuiz(c)}>
                    <div className="flex items-center justify-between">
                      <span className="chip">{c.category || "未分类"}</span>
                      {record?.passed ? (
                        <span className="flex items-center gap-1 text-[10.5px] text-ok">
                          <Check size={11} /> 已通过
                        </span>
                      ) : record && record.attempts > 0 ? (
                        <span className="text-[10.5px] text-warn">最好 {record.best} 分</span>
                      ) : (
                        <span className="text-[10.5px] text-faint">未开始</span>
                      )}
                    </div>
                    <div className="text-[14.5px] text-ink mt-2.5">{c.title}</div>
                    <div className="text-[11.5px] text-muted mt-1">
                      {c.sections.length} 个小节 · 面向 {c.audience || "全员"}
                    </div>
                    <div className="mt-3 flex items-center gap-1 text-[11.5px] text-accent opacity-0 group-hover:opacity-100 transition-opacity">
                      开始测验 <ArrowRight size={12} />
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        </div>

        <div>
          <SectionTitle title="问教练" desc="不知道学什么就问它" />
          <CoachPanel />
        </div>
      </div>

      {(active || loadingQuiz) && (
        <div className="fixed inset-0 z-40 bg-ink/25 backdrop-blur-[2px] flex items-center justify-center p-4 animate-fade">
          <div className="panel w-full max-w-2xl max-h-[86vh] overflow-y-auto animate-scale-in">
            <div className="sticky top-0 bg-panel border-b border-line px-6 py-4 flex items-center justify-between">
              <div>
                <div className="text-[11px] text-faint">章节测验</div>
                <div className="display text-[17px] text-ink">{active?.title}</div>
              </div>
              <button
                className="btn btn-ghost !p-2"
                onClick={() => {
                  setActive(null);
                  setQuiz(null);
                  setResult(null);
                }}
              >
                <X size={16} />
              </button>
            </div>

            <div className="px-6 py-5">
              {loadingQuiz && <Loading label="正在根据资料出题" />}
              {!loadingQuiz && quiz && (
                <>
                  {quiz.questions.map((q, qi) => {
                    const picked = answers[qi];
                    return (
                      <div key={qi} className="mb-6 last:mb-0">
                        <div className="text-[13.5px] text-ink leading-relaxed">
                          <span className="font-mono text-[11px] text-accent mr-2">{qi + 1}</span>
                          {q.question}
                        </div>
                        <div className="mt-3 space-y-2">
                          {q.options.map((opt, oi) => {
                            const chosen = picked === oi;
                            const correct = result && result.details[qi]?.correct_answer === oi;
                            const wrong = result && chosen && !correct;
                            return (
                              <button
                                key={oi}
                                disabled={!!result}
                                onClick={() => {
                                  const next = [...answers];
                                  next[qi] = oi;
                                  setAnswers(next);
                                }}
                                className={`w-full text-left rounded-[11px] border px-3.5 py-2.5 text-[13px] transition-all duration-200 ${
                                  correct
                                    ? "border-ok bg-[#f0f5ee] text-ink"
                                    : wrong
                                      ? "border-bad bg-[#fbf1ee] text-ink"
                                      : chosen
                                        ? "border-accent bg-accent-soft text-ink"
                                        : "border-line text-ink-soft hover:border-faint"
                                }`}
                              >
                                <span className="font-mono text-[11px] text-faint mr-2">
                                  {String.fromCharCode(65 + oi)}
                                </span>
                                {opt}
                              </button>
                            );
                          })}
                        </div>
                        {result && (
                          <div className="mt-2 text-[12px] text-muted leading-relaxed pl-1">
                            {result.details[qi]?.explanation || "见原文对应小节。"}
                          </div>
                        )}
                      </div>
                    );
                  })}

                  <div className="sticky bottom-0 bg-panel pt-3 pb-1 border-t border-line mt-4">
                    {result ? (
                      <div className="flex items-center justify-between">
                        <div className="text-[13px]">
                          <span className={result.passed ? "text-ok" : "text-warn"}>
                            {result.score} / {result.total}
                          </span>
                          <span className="text-muted ml-2">
                            {result.passed ? "通过，学情已更新" : "未到 80%，可以再来一次"}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <Button variant="ghost" onClick={() => active && startQuiz(active)}>
                            再考一次
                          </Button>
                          <Button
                            variant="primary"
                            onClick={() => {
                              setActive(null);
                              setQuiz(null);
                              setResult(null);
                            }}
                          >
                            完成
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between">
                        <div className="text-[12px] text-muted">
                          已答 {answers.filter((a) => a >= 0).length} / {quiz.questions.length}
                        </div>
                        <Button
                          variant="primary"
                          onClick={submit}
                          disabled={answers.some((a) => a < 0)}
                        >
                          提交
                        </Button>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CoachPanel() {
  const [messages, setMessages] = useState<Array<{ role: "user" | "assistant"; text: string }>>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId] = useState(newSessionId);

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", text: message }, { role: "assistant", text: "" }]);
    let answer = "";
    try {
      await api.coach(message, sessionId, {
        onToken: (t) => {
          answer += t;
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", text: answer };
            return next;
          });
        },
        onMeta: () => undefined,
      });
    } catch (e) {
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: "assistant", text: (e as Error).message };
        return next;
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="!p-4">
      <div className="space-y-3 min-h-[120px] max-h-[420px] overflow-y-auto">
        {messages.length === 0 && (
          <div className="text-[12.5px] text-muted leading-relaxed">
            可以问我「我哪章最弱」「考考我考勤」，我会去查资料或直接出题。
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "text-right" : ""}>
            <div
              className={`inline-block text-left rounded-[12px] px-3 py-2 text-[12.5px] leading-relaxed ${
                m.role === "user" ? "bg-accent-soft text-ink" : "bg-paper text-ink-soft"
              }`}
            >
              {m.text || (busy && i === messages.length - 1 ? <Typing /> : "")}
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-3">
        <input
          className="input !py-2"
          placeholder="问教练…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
        />
        <Button variant="primary" onClick={() => send()} disabled={busy || !input.trim()} className="!px-3">
          <MessageCircle size={14} />
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5 mt-2.5">
        {["我哪章最弱", "考考我考勤"].map((q) => (
          <button
            key={q}
            onClick={() => send(q)}
            className="rounded-full border border-line px-2.5 py-1 text-[11px] text-muted hover:text-accent hover:border-accent/40 transition-colors"
          >
            {q}
          </button>
        ))}
      </div>
    </Card>
  );
}
