import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";

export function Button({
  children,
  variant = "secondary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  const map: Record<string, string> = {
    primary: "btn-primary",
    secondary: "btn-secondary",
    ghost: "btn-ghost",
    danger: "btn-danger",
  };
  return (
    <button className={`btn ${map[variant]} ${className}`} {...props}>
      {children}
    </button>
  );
}

export function Card({
  children,
  className = "",
  style,
  hover = false,
}: {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
  hover?: boolean;
}) {
  return (
    <div className={`panel p-5 ${hover ? "panel-hover" : ""} ${className}`} style={style}>
      {children}
    </div>
  );
}

export function SectionTitle({
  title,
  desc,
  right,
}: {
  title: string;
  desc?: string;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-4 mb-4">
      <div>
        <div className="display text-[21px] text-ink">{title}</div>
        {desc ? <div className="text-[14px] text-muted mt-1">{desc}</div> : null}
      </div>
      {right}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="panel px-4 py-3.5">
      <div className="text-[12.5px] text-faint tracking-wide">{label}</div>
      <div className="display text-[26px] text-ink mt-1 leading-none">{value}</div>
      {hint ? <div className="text-[12.5px] text-muted mt-1.5">{hint}</div> : null}
    </div>
  );
}

export function EmptyState({
  title,
  desc,
  action,
}: {
  title: string;
  desc?: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel py-14 px-8 text-center animate-fade">
      <div className="display text-[18.5px] text-ink">{title}</div>
      {desc ? (
        <p className="text-[14px] text-muted mt-2 max-w-sm mx-auto leading-relaxed">{desc}</p>
      ) : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function Loading({ label = "加载中" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-[14px] text-muted">
      <Loader2 size={14} className="animate-spin" />
      {label}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: i === 0 ? 56 : 44, opacity: 1 - i * 0.12 }} />
      ))}
    </div>
  );
}

export function Progress({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return (
    <div className="progress-track">
      <div className="progress-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

/** 打字指示：三个点依次呼吸，比"正在输入…"更轻 */
export function Typing() {
  return (
    <div className="flex items-center gap-1.5 py-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-accent"
          style={{ animation: "blink 1.1s ease-in-out infinite", animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </div>
  );
}
