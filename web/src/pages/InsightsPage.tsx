import { useEffect, useState } from "react";
import { AlertCircle, ArrowUpRight, Timer } from "lucide-react";
import { api } from "../lib/api";
import type { Mastery, Metrics } from "../lib/types";
import { Card, EmptyState, Progress, SectionTitle, Skeleton, Stat } from "../components/ui";

export default function InsightsPage() {
  const [data, setData] = useState<Mastery | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);

  useEffect(() => {
    api.mastery().then(setData).catch(() => setData(null));
    api.metrics(7).then(setMetrics).catch(() => undefined);
  }, []);

  if (!data) return <div className="max-w-3xl"><Skeleton rows={3} /></div>;
  if (data.studied === 0) {
    return (
      <div className="max-w-3xl">
        <EmptyState
          title="还没有学习记录"
          desc="去「培训」页选一章，做完测验，这里就会长出你的学情。"
        />
      </div>
    );
  }

  return (
    <div className="max-w-4xl space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger">
        <Stat label="完成率" value={`${(data.completion * 100).toFixed(0)}%`} hint={`${data.passed} / ${data.total_chapters} 章通过`} />
        <Stat label="已学章节" value={data.studied} hint="做过测验的章节数" />
        <Stat label="待补章节" value={data.weak.length} hint="考过但没到 80%" />
        <Stat label="未开始" value={data.total_chapters - data.studied} hint="还没碰过" />
      </div>

      <div className="grid lg:grid-cols-[280px_1fr] gap-5 items-start">
        <Card className="!p-6 flex flex-col items-center">
          <Ring value={data.completion} />
          <div className="text-[11.5px] text-muted mt-4 text-center leading-relaxed">
            完成 {data.passed} 章
            <br />
            共 {data.total_chapters} 章
          </div>
        </Card>

        <div>
          <SectionTitle title="章节掌握" desc="按最好成绩排序，靠前的是最该补的" />
          <div className="space-y-2 stagger">
            {[...data.chapters]
              .sort((a, b) => Number(a.passed) - Number(b.passed) || a.best - b.best)
              .map((c) => {
                const ratio = c.attempts ? c.best / Math.max(1, c.best || 1) : 0;
                const pct = c.attempts ? Math.min(100, (c.best / 5) * 100) : 0;
                return (
                  <Card key={c.chapter} className="!p-3.5" hover>
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-[13px] text-ink truncate">{c.title}</div>
                        <div className="text-[11px] text-faint mt-0.5">
                          {c.category} · {c.attempts ? `考过 ${c.attempts} 次` : "未开始"}
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <div className={`text-[12px] ${c.passed ? "text-ok" : c.attempts ? "text-warn" : "text-faint"}`}>
                          {c.attempts ? `${c.best} / 5` : "—"}
                        </div>
                      </div>
                    </div>
                    <div className="mt-2.5">
                      <Progress value={pct} max={100} />
                    </div>
                    {ratio === 0 && c.attempts > 0 && (
                      <div className="text-[11px] text-warn mt-1.5">还没到 80%，建议再看一遍原文</div>
                    )}
                  </Card>
                );
              })}
          </div>
        </div>
      </div>

      {data.weak.length > 0 && (
        <Card className="!p-5 border-[#e6d3c3] bg-[#fdf8f2]">
          <div className="flex items-start gap-3">
            <AlertCircle size={15} className="text-warn mt-0.5 shrink-0" />
            <div>
              <div className="text-[13px] text-ink">最该补的三章</div>
              <div className="text-[12px] text-muted mt-1 leading-relaxed">
                {data.weak.map((w) => w.title).join("、")}
                {data.untouched.length > 0 && `；另外 ${data.untouched.slice(0, 3).join("、")} 还没开始。`}
              </div>
            </div>
          </div>
        </Card>
      )}

      {metrics && (
        <div>
          <SectionTitle title="近 7 天调用" desc="成本与延迟按真实调用记账，不是估算" />
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Card className="!p-4">
              <div className="text-[11px] text-faint flex items-center gap-1">
                <ArrowUpRight size={11} /> 调用次数
              </div>
              <div className="display text-[20px] text-ink mt-1">{metrics.calls}</div>
            </Card>
            <Card className="!p-4">
              <div className="text-[11px] text-faint">成本</div>
              <div className="display text-[20px] text-ink mt-1">¥{metrics.cost.toFixed(4)}</div>
            </Card>
            <Card className="!p-4">
              <div className="text-[11px] text-faint flex items-center gap-1">
                <Timer size={11} /> 平均延迟
              </div>
              <div className="display text-[20px] text-ink mt-1">{metrics.avg_latency_ms}ms</div>
            </Card>
            <Card className="!p-4">
              <div className="text-[11px] text-faint">P95 延迟</div>
              <div className="display text-[20px] text-ink mt-1">{metrics.p95_latency_ms}ms</div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function Ring({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value));
  const r = 52;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative">
      <svg width="132" height="132" viewBox="0 0 132 132" className="-rotate-90">
        <circle cx="66" cy="66" r={r} fill="none" stroke="var(--color-line-2)" strokeWidth="7" />
        <circle
          cx="66"
          cy="66"
          r={r}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          style={{ transition: "stroke-dashoffset 1.1s cubic-bezier(0.22,1,0.36,1)" }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <div className="display text-[26px] text-ink">{(pct * 100).toFixed(0)}%</div>
      </div>
    </div>
  );
}
