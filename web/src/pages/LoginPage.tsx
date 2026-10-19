import { useState } from "react";
import { Flame, LogIn } from "lucide-react";
import { api, setToken } from "../lib/api";
import type { User } from "../lib/types";
import { Button } from "../components/ui";

const DEMO = [
  { username: "admin", password: "admin12345", hint: "管理员 · 可见全部资料" },
  { username: "sales", password: "sales12345", hint: "销售 · 看不到客服口径" },
  { username: "support", password: "support12345", hint: "客服 · 看不到销售话术" },
];

export default function LoginPage({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("sales");
  const [password, setPassword] = useState("sales12345");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.login(username.trim(), password);
      setToken(res.token);
      onLogin(res.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen grid place-items-center px-6 relative z-[1]">
      <div className="w-full max-w-[420px]">
        <div className="flex items-center gap-2.5 mb-8">
          <div className="h-10 w-10 rounded-[13px] bg-accent text-[#fff8f2] grid place-items-center">
            <Flame size={18} strokeWidth={2} />
          </div>
          <div className="leading-tight">
            <div className="display text-[20px] text-ink">薪火</div>
            <div className="font-mono text-[11px] tracking-[0.18em] text-faint uppercase">Ember</div>
          </div>
        </div>

        <h1 className="display text-[28px] text-ink">登录后才知道你能看哪些资料</h1>
        <p className="text-[14.5px] text-muted mt-2 leading-relaxed">
          知识库按部门授权：销售看不到客服口径，客服看不到销售话术，管理员可见全部。
        </p>

        <div className="panel mt-6 space-y-3">
          <div>
            <label className="text-[12.5px] text-faint">账号</label>
            <input
              className="input mt-1"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && submit()}
              autoComplete="username"
            />
          </div>
          <div>
            <label className="text-[12.5px] text-faint">密码</label>
            <input
              className="input mt-1"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && submit()}
              autoComplete="current-password"
            />
          </div>
          {error && <div className="text-[13.5px] text-bad">{error}</div>}
          <Button variant="primary" className="w-full !justify-center" onClick={submit} disabled={busy}>
            {busy ? "登录中…" : "登录"}
            {!busy && <LogIn size={13} className="ml-1.5" />}
          </Button>
        </div>

        <div className="mt-6">
          <div className="text-[12.5px] text-faint mb-2">演示账号（点一下填入）</div>
          <div className="space-y-1.5">
            {DEMO.map((d) => (
              <button
                key={d.username}
                onClick={() => {
                  setUsername(d.username);
                  setPassword(d.password);
                }}
                className="w-full flex items-center justify-between rounded-[10px] border border-line bg-panel px-3 py-2 text-left hover:border-accent/40 transition-colors"
              >
                <span className="font-mono text-[13px] text-ink-soft">{d.username}</span>
                <span className="text-[12.5px] text-faint">{d.hint}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
