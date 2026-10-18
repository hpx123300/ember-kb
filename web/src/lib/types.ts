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
  chapter: string;
  questions: QuizQuestion[];
  answers: number[];
  explanations: string[];
}

export interface QuizResult {
  score: number;
  total: number;
  passed: boolean;
  ratio: number;
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
  total_chapters: number;
  studied: number;
  passed: number;
  completion: number;
  chapters: MasteryChapter[];
  weak: MasteryChapter[];
  untouched: string[];
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
