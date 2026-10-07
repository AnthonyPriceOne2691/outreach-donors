/**
 * Настройки агента переписки (`/api/agent/settings`) и решения по его
 * черновикам (`/api/agent/drafts`).
 *
 * Типы лежат здесь, а не в `types.ts`: тот уже за пределом длины файла.
 */

import { request } from './client';
import type { SendResult } from './types';

/** Как агент ведёт разговор на одном этапе — то, что правит человек. */
export interface AgentSettingsBody {
  enabled: boolean;
  goal: string;
  tone: string;
  points: string[];
  /** Доллары строкой, как все деньги сервера. У доноров — не дороже,
   *  у рекламодателей — не дешевле; `null` — предела нет. */
  price_limit_usd: string | null;
  stop_topics: string[];
  /** Режим агента; в ответе сервера есть всегда, не прислан — сервер оставит текущий. */
  mode?: 'drafts' | 'autopilot' | undefined;
  /** Ответов автопилота в одной переписке; не прислан — тоже останется текущий. */
  max_turns?: number | undefined;
}

export interface AgentSettingsVersion {
  version: number;
  created_by: string | null;
  created_at: string;
  settings: AgentSettingsBody;
}

export interface AgentStageView {
  /** Этап из реестра сервера: своего списка этапов у экрана нет. */
  stage: string;
  /** Кому агент пишет на этапе — имя в переключателе — и что он там делает. */
  title: string;
  lead: string;
  /** Предел цены — «не дороже» (`buy`, мы покупаем) или «не дешевле» (`sell`). */
  price_side: 'buy' | 'sell';
  /** `null` — этап не настраивали, и агент на нём не пишет. */
  current: AgentSettingsVersion | null;
  defaults: AgentSettingsBody;
  history: AgentSettingsVersion[];
}

export interface AgentView {
  stages: AgentStageView[];
}

export function fetchAgentSettings(): Promise<AgentView> {
  return request<AgentView>('/agent/settings');
}

export function saveAgentSettings(
  stage: string,
  body: AgentSettingsBody,
): Promise<AgentSettingsVersion> {
  return request<AgentSettingsVersion>(`/agent/settings/${stage}`, { method: 'POST', body });
}

/** Где черновик агента в жизни ответа (`agent_drafts.status`). */
export type DraftStatus = 'drafted' | 'skipped' | 'escalated' | 'sent' | 'rejected';

/** Черновик агента под ответом собеседника — как его отдаёт переписка. */
export interface DraftCard {
  id: number;
  /** Ответ собеседника, на который черновик написан. */
  reply_id: number;
  thread_id: number | null;
  status: DraftStatus;
  /** Пусто — текста нет: ответ не нужен или агент сразу отдал его человеку. */
  body: string;
  /** Почему отдан человеку или пропущен — словами. */
  reason: string | null;
  settings_version: number;
  written_at: string;
  decided_by: string | null;
  decided_at: string | null;
  /** Последний вердикт судьи этапа; судьи не было — `null`. */
  verdict: 'allow' | 'block' | 'escalate' | null;
  /** Сколько раз писатель писал под проверкой судьи. */
  attempts: number;
}

/** Что переписка (`GET /api/threads/{id}`) знает об агенте — сверх `ThreadView`. */
export interface ThreadAgent {
  drafts?: DraftCard[];
  agent_reasons?: string[];
}

/** `body` пусто — «как есть»; иначе — с правкой. */
export function sendDraft(id: number, body: string | null): Promise<SendResult> {
  return request<SendResult>(`/agent/drafts/${id}/send`, {
    method: 'POST',
    body: body === null ? {} : { body },
  });
}

export function rejectDraft(id: number, reason: string): Promise<DraftCard> {
  return request<DraftCard>(`/agent/drafts/${id}/reject`, { method: 'POST', body: { reason } });
}
