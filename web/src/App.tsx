import { useEffect, useState } from "react";
import AppShell, { type PageKey } from "./components/AppShell";
import ErrorBoundary from "./components/ErrorBoundary";
import AskPage from "./pages/AskPage";
import LearnPage from "./pages/LearnPage";
import InsightsPage from "./pages/InsightsPage";
import LibraryPage from "./pages/LibraryPage";
import LoginPage from "./pages/LoginPage";
import { api, getToken, setToken } from "./lib/api";
import type { User } from "./lib/types";

const PAGES: PageKey[] = ["ask", "learn", "insights", "library"];

function pageFromHash(): PageKey {
  const hash = window.location.hash.replace("#", "") as PageKey;
  return PAGES.includes(hash) ? hash : "ask";
}

export default function App() {
  // 用 hash 记住当前页：刷新和分享链接都能落回同一页，
  // 内部工具里「把链接发给同事」是高频动作，刷新回首页很烦人。
  const [page, setPage] = useState<PageKey>(pageFromHash);
  // 切页时重置错误边界：一个页面崩了不该连累其他页面
  const [epoch, setEpoch] = useState(0);
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState<boolean>(() => Boolean(getToken()));

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // 有本地凭证就先验一次：过期的 token 不该让页面白屏，应该退回登录
  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setChecking(false));
  }, []);

  // 任意请求收到 401 都会广播，这里统一退出，各页面不用各写一遍
  useEffect(() => {
    const onLogout = () => setUser(null);
    window.addEventListener("ember:logout", onLogout);
    return () => window.removeEventListener("ember:logout", onLogout);
  }, []);

  if (checking) {
    return (
      <div className="min-h-screen grid place-items-center">
        <div className="font-mono text-[11px] tracking-[0.2em] text-faint uppercase">loading</div>
      </div>
    );
  }

  if (!user) {
    return <LoginPage onLogin={setUser} />;
  }

  return (
    <AppShell
      user={user}
      page={page}
      onLogout={() => {
        setToken(null);
        setUser(null);
      }}
      onNavigate={(p) => {
        setPage(p);
        window.location.hash = p;
        setEpoch((n) => n + 1);
      }}
    >
      <ErrorBoundary key={`${page}-${epoch}`} onReset={() => setEpoch((n) => n + 1)}>
        {page === "ask" && <AskPage />}
        {page === "learn" && <LearnPage />}
        {page === "insights" && <InsightsPage />}
        {page === "library" && <LibraryPage />}
      </ErrorBoundary>
    </AppShell>
  );
}
