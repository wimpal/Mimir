export type HealthBadge = {
  badge: "ready" | "degraded" | "unreachable" | string;
  status: string;
  detail: string;
};

export type AppSettings = {
  brain_url: string;
  conversation_id: string | null;
  token: string | null;
  has_token: boolean;
};

export type ConversationSummary = {
  id: string;
  created_at?: string | null;
  updated_at?: string | null;
  preview?: string | null;
  message_count?: number | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "error" | "system";
  content: string;
  streaming?: boolean;
  showWriteConfirm?: boolean;
};

export type SseEvent =
  | { type: "meta"; conversation_id?: string }
  | { type: "token"; text: string }
  | { type: "sentence"; index: number; text: string }
  | { type: "tool_start"; name: string; arguments?: unknown }
  | { type: "tool_end"; name: string; ok: boolean; result_preview?: string }
  | {
      type: "done";
      stopped_reason?: string;
      tools_used?: string[];
      turn_id?: string;
      conversation_id?: string;
    }
  | { type: "error"; message: string; conversation_id?: string }
  | { type: string; [key: string]: unknown };

export type ChatEventEnvelope = {
  stream_id: number;
  event: SseEvent;
};

export type ChatClosedEnvelope = {
  stream_id: number;
  reason: string;
};

export type LaunchResult = {
  already_running: boolean;
  started: boolean;
  message: string;
  pid?: number | null;
};
