import { useEffect, useState } from "react";
import { FileText, RefreshCw, Trash2, Upload } from "lucide-react";
import { api } from "../lib/api";
import type { Doc, LibStats } from "../lib/types";
import { Button, Card, EmptyState, SectionTitle, Skeleton, Stat } from "../components/ui";

const TEMPLATE = `---
category: 制度
audience: 全员
---
# 文档标题

## 小节一
这里写正文。检索会按小节切块，所以小节标题要能概括内容。

## 小节二
金额、时限这类关键信息写清楚，回答会原样引用。`;

export default function LibraryPage() {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [stats, setStats] = useState<LibStats | null>(null);
  const [filename, setFilename] = useState("");
  const [content, setContent] = useState(TEMPLATE);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  async function refresh() {
    const [d, s] = await Promise.all([api.docs(), api.libStats()]);
    setDocs(d);
    setStats(s);
  }

  useEffect(() => {
    refresh().catch(() => setDocs([]));
  }, []);

  async function upload() {
    if (!filename.trim() || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const name = filename.trim().endsWith(".md") ? filename.trim() : `${filename.trim()}.md`;
      const res = await api.createDoc(name, content);
      setMsg({ kind: "ok", text: `已收录「${name}」，切出 ${res.sections} 个片段，索引已重建` });
      setFilename("");
      setContent(TEMPLATE);
      await refresh();
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (!confirm(`删除「${name}」？删除后相关问答会查不到内容。`)) return;
    try {
      await api.deleteDoc(name);
      await refresh();
      setMsg({ kind: "ok", text: `已删除 ${name}` });
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    }
  }

  if (docs === null) return <div className="max-w-4xl"><Skeleton rows={3} /></div>;

  return (
    <div className="max-w-4xl space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger">
        <Stat label="文档" value={stats?.docs ?? docs.length} hint="Markdown 资料数" />
        <Stat label="检索片段" value={stats?.chunks ?? "—"} hint="按小节切块后的数量" />
        <Stat label="类别" value={stats?.categories.length ?? 0} hint={stats?.categories.join(" / ") || "—"} />
        <Stat label="总字数" value={stats?.chars ?? 0} hint="不含元信息" />
      </div>

      <div>
        <SectionTitle title="已有资料" desc="改完即生效，检索索引按文件指纹自动重建" />
        {docs.length === 0 ? (
          <EmptyState title="还没有资料" desc="用下面的表单上传第一份 Markdown。" />
        ) : (
          <div className="space-y-2 stagger">
            {docs.map((d) => (
              <Card key={d.filename} className="!p-3.5" hover>
                <div className="flex items-center gap-3">
                  <div className="h-8 w-8 rounded-[9px] bg-paper grid place-items-center shrink-0">
                    <FileText size={14} className="text-accent" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[14.5px] text-ink truncate">{d.title}</div>
                    <div className="text-[12.5px] text-faint mt-0.5 font-mono truncate">
                      {d.filename} · {d.sections} 段 · {d.chars} 字 · {d.updated_at}
                    </div>
                  </div>
                  <span className="chip shrink-0">{d.category || "未分类"}</span>
                  <button
                    onClick={() => remove(d.filename)}
                    className="btn btn-ghost !p-2 text-faint hover:text-bad"
                    title="删除"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      <div>
        <SectionTitle title="新增资料" desc="必须带 category 元信息，否则无法按类别检索" />
        <Card className="!p-5">
          <div className="flex gap-2 items-center mb-3">
            <input
              className="input !w-64"
              placeholder="文件名，如 handbook-travel.md"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
            <Button variant="primary" onClick={upload} disabled={busy || !filename.trim()}>
              {busy ? <RefreshCw size={13} className="animate-spin" /> : <Upload size={13} />}
              收录
            </Button>
          </div>
          <textarea
            className="input font-mono !text-[13.5px] leading-relaxed min-h-[220px] resize-y"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
          />
          {msg && (
            <div
              className={`mt-3 text-[13.5px] rounded-[9px] px-3 py-2 animate-fade ${
                msg.kind === "ok"
                  ? "bg-sage-soft text-ink-soft"
                  : "bg-[#fbf1ee] text-bad border border-[#eccfc7]"
              }`}
            >
              {msg.text}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
