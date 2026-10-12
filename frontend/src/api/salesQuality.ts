/**
 * Продажи: качество агента — как люди обходятся с черновиками агента (`GET /sales/agent-quality`).
 *
 * Типы — своим файлом, а не в `salesTypes.ts`: тот у предела длины. Имена полей — те же, что у
 * схем сервера (`backend/api/sales/calibration.py`); тест сервера сверяет их по этому файлу.
 */

import { request } from './client';

/** Черновики по местам — каждый ровно в одном. */
export interface QualityCounts {
  /** Решения оператора по готовому черновику — основа долей. */
  as_is: number;
  edited: number;
  rejected: number;
  /** Агент молчал («ответ не нужен»): человек тоже промолчал — или ответил. */
  true_silence: number;
  false_silence: number;
  /** Агент отдал человеку: отправлено, отклонено, ждёт. */
  held_sent: number;
  held_rejected: number;
  held_waiting: number;
  /** Готовый черновик ждёт решения или молчание агента ещё без исхода. */
  waiting: number;
  /** Ушло автоотправкой — не решение оператора. */
  auto: number;
}

export type GateState = 'few' | 'passed' | 'failed';

/** Ворота автоответа ситуации — итог и слова сервера. */
export interface GateView {
  state: GateState;
  said: string[];
}

/** Версия агента: номер для адреса, промпты, таблица ходов и база знаний. */
export interface CohortView {
  id: string;
  reply: string | null;
  situation: string | null;
  judge: string | null;
  moves: string | null;
  kb: string | null;
}

export interface CohortRow {
  cohort: CohortView;
  current: boolean;
  first_at: string;
  last_at: string;
  counts: QualityCounts;
}

export interface SituationRow {
  /** Метка агента; пусто — письмо отдано человеку до разбора. */
  situation: string | null;
  title: string;
  counts: QualityCounts;
  gate: GateView;
}

export interface ReasonRow {
  reason: string;
  count: number;
}

export type DiffKind = 'same' | 'removed' | 'added';

export interface DiffPart {
  kind: DiffKind;
  text: string;
}

export interface ChangeRow {
  draft_id: number;
  thread_id: number | null;
  situation: string | null;
  title: string;
  turn: number | null;
  decided_at: string | null;
  decision: 'edited' | 'rejected';
  held: boolean;
  held_reason: string | null;
  reason: string | null;
  draft: string;
  sent: string | null;
  parts: DiffPart[];
}

/** Пороги ворот: N решений и доли процентами (не ниже); молчание — исход через дни. */
export interface QualityLimits {
  min_n: number;
  as_is: number;
  silence: number;
  settle_days: number;
}

export interface AgentQualityView {
  limits: QualityLimits;
  current: CohortView;
  shown: CohortView;
  cohorts: CohortRow[];
  counts: QualityCounts;
  situations: SituationRow[];
  reasons: ReasonRow[];
  changes: ChangeRow[];
}

/** Качество агента — по версии `cohort`; пусто — текущая (или последняя писавшая). */
export function readAgentQuality(cohort: string): Promise<AgentQualityView> {
  const search = cohort === '' ? '' : `?cohort=${encodeURIComponent(cohort)}`;
  return request<AgentQualityView>(`/sales/agent-quality${search}`);
}
