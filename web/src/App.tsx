import { useEffect, useState } from "react";
import AppShell, { type PageKey } from "./components/AppShell";
import ErrorBoundary from "./components/ErrorBoundary";
import AskPage from "./pages/AskPage";
import LearnPage from "./pages/LearnPage";
import InsightsPage from "./pages/InsightsPage";
import LibraryPage from "./pages/LibraryPage";

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

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <AppShell
      page={page}
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
