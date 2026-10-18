import type {
  AskMeta,
  AssistantReply,
  Chapter,
  Doc,
  LibStats,
  Mastery,
  Metrics,
  Quiz,
  QuizResult,
  Trace,
  User,
} from "./types";

const TOKEN_KEY = "ember_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式下 localStorage 可能不可用，登录态就退化成仅本次会话 */
  }
}

/** 凭证失效时广播，由 App 统一踢回登录页——各个页面不用各写一遍跳转。 */
function notifyExpired(): void {
  setToken(null);
  window.dispatchEvent(new Event("ember:logout"));
}

function headers(extra?: HeadersInit): HeadersInit {
  const token = getToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(extra || {}),
  };
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { headers: headers(init?.headers), ...init });
  if (res.status === 401) {
    notifyExpired();
    throw new Error("登录已失效，请重新登录");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.error || data?.detail || `请求失败（${res.status}）`);
  }
  return data as T;
}

/** 问答流式事件的消费回调。meta 在 token 结束后到达，携带引用与拒答判定。 */
export interface AskHandlers {
  onToken: (text: string) => void;
  onMeta: (meta: AskMeta) => void;
}

async function consumeSSE(
  url: string,
  body: unknown,
  handlers: Record<string, (payload: any) => void>,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) {
    notifyExpired();
    throw new Error("登录已失效，请重新登录");
  }
  if (!res.ok || !res.body) throw new Error(`请求失败（${res.status}）`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload) continue;
      try {
        const msg = JSON.parse(payload);
        if (msg.type === "error") throw new Error(msg.error || "服务异常");
        handlers[msg.type]?.(msg);
      } catch (e) {
        if (e instanceof Error && !e.message.includes("JSON")) throw e;
      }
    }
  }
}

export const api = {
  login: (username: string, password: string) =>
    request<{ token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<User>("/api/auth/me"),

  ask: (question: string, category: string, handlers: AskHandlers, signal?: AbortSignal) =>
    consumeSSE(
      "/api/ask",
      { question, category },
      {
        token: (m) => handlers.onToken(m.text),
        meta: (m) => handlers.onMeta(m as AskMeta),
      },
      signal,
    ),

  /** 多 Agent 协作：由调度员决定找知识库、教练还是学情。 */
  assistant: (question: string) =>
    request<AssistantReply>("/api/assistant", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

  coach: (
    message: string,
    sessionId: string,
    handlers: { onToken: (t: string) => void; onMeta: (m: any) => void },
    signal?: AbortSignal,
  ) =>
    consumeSSE(
      "/api/coach",
      { message, session_id: sessionId },
      { token: (m) => handlers.onToken(m.text), meta: (m) => handlers.onMeta(m) },
      signal,
    ),

  chapters: () => request<Chapter[]>("/api/chapters"),
  quiz: (chapter: string, count = 5) =>
    request<Quiz>("/api/quiz", { method: "POST", body: JSON.stringify({ chapter, count }) }),
  /** 只提交作答；标准答案在服务端，客户端拿不到也改不了。 */
  submitQuiz: (sessionId: string, answers: number[]) =>
    request<QuizResult>("/api/quiz/submit", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, answers }),
    }),
  mastery: () => request<Mastery>("/api/mastery"),

  docs: () => request<Doc[]>("/api/library/docs"),
  libStats: () => request<LibStats>("/api/library/stats"),
  createDoc: (filename: string, content: string) =>
    request<{ filename: string; sections: number }>("/api/library/docs", {
      method: "POST",
      body: JSON.stringify({ filename, content }),
    }),
  deleteDoc: (filename: string) =>
    request<{ ok: boolean }>(`/api/library/docs/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),

  metrics: (days = 7) => request<Metrics>(`/api/metrics?days=${days}`),
  traces: (limit = 20) => request<Trace[]>(`/api/traces?limit=${limit}`),
  trace: (id: string) => request<Trace>(`/api/traces/${id}`),
};

export function newSessionId(): string {
  try {
    return `coach-${crypto.randomUUID().slice(0, 8)}`;
  } catch {
    return `coach-${Math.random().toString(36).slice(2, 10)}`;
  }
}
