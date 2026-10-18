export interface Citation {
  index: number;
  title: string;
  source: string;
  cite: string;
  snippet: string;
}

export interface AskMeta {
  refused: boolean;
  coverage: number;
  citations: Citation[];
  /** 本次请求的链路 id，可在管理端回放每一步耗时 */
  trace_id?: string;
  total_ms?: number;
}

export interface Chapter {
  id: string;
  title: string;
  category: string;
  audience: string;
  sections: string[];
}

export interface QuizQuestion {
  question: string;
  options: string[];
  knowledge_point: string;
}

export interface Quiz {
  session_id: string;
  chapter: string;
  chapter_title: string;
  count: number;
  questions: QuizQuestion[];
}

export interface QuizDetail {
  index: number;
  your_answer: number;
  correct_answer: number;
  ok: boolean;
  explanation: string;
}

export interface QuizResult {
  score: number;
  total: number;
  passed: boolean;
  ratio: number;
  pass_ratio: number;
  details: QuizDetail[];
}

export interface MasteryChapter {
  chapter: string;
  title: string;
  category: string;
  sections: number;
  attempts: number;
  best: number;
  passed: boolean;
  last: string | null;
}

export interface Mastery {
  employee: string;
  total_chapters: number;
  studied: number;
  passed: number;
  completion: number;
  chapters: MasteryChapter[];
  weak: Array<{ title: string; best: number; attempts: number }>;
  untouched: string[];
  recent_questions: string[];
}

export interface Doc {
  filename: string;
  title: string;
  category: string;
  audience: string;
  sections: number;
  chars: number;
  updated_at: string;
}

export interface LibStats {
  docs: number;
  chunks: number;
  categories: string[];
  chars: number;
}

export interface User {
  id: number;
  username: string;
  name: string;
  department: string;
  role: string;
  audiences: string[];
}

export interface TraceStep {
  name: string;
  ms: number;
  ok: boolean;
  detail: string;
}

export interface Trace {
  trace_id: string;
  kind: string;
  user: string;
  agent: string;
  question: string;
  refused: boolean;
  ok: boolean;
  error: string;
  total_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  steps: TraceStep[];
  created_at: string | null;
}

export interface AssistantReply {
  agent: string;
  agent_key: string;
  reason: string;
  answer: string;
  citations: Citation[];
  refused: boolean;
  trace_id: string;
  total_ms: number;
  steps: TraceStep[];
}

export interface Metrics {
  window_days: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number;
  cost_per_call: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  by_tool: Record<string, { calls: number; success_rate: number | null; avg_latency_ms: number }>;
}
