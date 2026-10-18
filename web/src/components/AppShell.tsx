import type { ReactNode } from "react";
import { BookOpen, Compass, Flame, Library } from "lucide-react";

export type PageKey = "ask" | "learn" | "insights" | "library";

const NAV: Array<{ key: PageKey; label: string; icon: typeof Compass }> = [
  { key: "ask", label: "问答", icon: Compass },
  { key: "learn", label: "培训", icon: BookOpen },
  { key: "insights", label: "学情", icon: Flame },
  { key: "library", label: "资料", icon: Library },
];

const TITLES: Record<PageKey, { eyebrow: string; title: string; desc: string }> = {
  ask: { eyebrow: "Ask", title: "问一句，答一句", desc: "每个结论都带出处，查不到会直说" },
  learn: { eyebrow: "Learn", title: "按章节学，学完就考", desc: "资料即课程，测验结果自动进学情" },
  insights: { eyebrow: "Insights", title: "你的学情", desc: "哪章过了、哪章最该补，一眼看完" },
  library: { eyebrow: "Library", title: "资料库", desc: "上传 Markdown，索引自动重建" },
};

export default function AppShell({
  page,
  onNavigate,
  children,
}: {
  page: PageKey;
  onNavigate: (p: PageKey) => void;
  children: ReactNode;
}) {
  const meta = TITLES[page];
  return (
    <div className="min-h-screen flex relative z-[1]">
      <aside className="hidden md:flex w-[228px] shrink-0 flex-col border-r border-line bg-[#f6f1e7] px-4 py-6">
        <div className="flex items-center gap-2.5 px-2 mb-9">
          <div className="h-9 w-9 rounded-[12px] bg-accent text-[#fff8f2] grid place-items-center shadow-[0_4px_12px_-6px_rgba(192,101,63,0.9)]">
            <Flame size={17} strokeWidth={2} />
          </div>
          <div className="leading-tight">
            <div className="display text-[16px] text-ink">薪火</div>
            <div className="font-mono text-[9.5px] tracking-[0.18em] text-faint uppercase">Ember</div>
          </div>
        </div>

        <nav className="space-y-0.5">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = page === item.key;
            return (
              <button
                key={item.key}
                onClick={() => onNavigate(item.key)}
                className={`relative w-full flex items-center gap-2.5 rounded-[11px] px-3 py-2.5 text-[13px] transition-colors duration-200 ${
                  active
                    ? "bg-panel text-ink border border-line"
                    : "text-muted hover:text-ink hover:bg-panel/60 border border-transparent"
                }`}
              >
                {active && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-[2px] rounded-r bg-accent" />
                )}
                <Icon size={15} strokeWidth={active ? 2.1 : 1.8} className={active ? "text-accent" : ""} />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto px-2 pt-8 space-y-1.5">
          <div className="text-[10.5px] leading-relaxed text-faint">知识库 · 本地部署</div>
          <div className="text-[10.5px] leading-relaxed text-faint">数据不出内网，回答只依据资料</div>
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <div className="md:hidden flex gap-1 overflow-x-auto px-4 pt-4 pb-1 border-b border-line bg-paper">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = page === item.key;
            return (
              <button
                key={item.key}
                onClick={() => onNavigate(item.key)}
                className={`flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[12px] whitespace-nowrap transition-colors ${
                  active ? "bg-accent text-[#fff8f2]" : "bg-panel text-muted border border-line"
                }`}
              >
                <Icon size={12} />
                {item.label}
              </button>
            );
          })}
        </div>

        <header className="px-6 md:px-12 pt-8 md:pt-12 pb-2">
          <div className="eyebrow">{meta.eyebrow}</div>
          <h1 className="display text-[28px] md:text-[34px] mt-2 text-ink">{meta.title}</h1>
          <p className="text-[13px] text-muted mt-2">{meta.desc}</p>
        </header>

        <main className="flex-1 px-6 md:px-12 pb-20 pt-5">{children}</main>
      </div>
    </div>
  );
}
