/**
 * Настройки агента переписки (`/api/agent/settings`).
 *
 * Типы лежат здесь, а не в `types.ts`: тот уже за пределом длины файла.
 */

import { request } from './client';
import type { LetterStage } from './types';

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
}

export interface AgentSettingsVersion {
  version: number;
  created_by: string | null;
  created_at: string;
  settings: AgentSettingsBody;
}

export interface AgentStageView {
  stage: LetterStage;
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
  stage: LetterStage,
  body: AgentSettingsBody,
): Promise<AgentSettingsVersion> {
  return request<AgentSettingsVersion>(`/agent/settings/${stage}`, { method: 'POST', body });
}
