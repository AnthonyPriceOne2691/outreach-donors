/**
 * Домены рассылки по этапу: направление ящика, лимиты доменов и направлений (Ф4, 4.5a).
 *
 * Лимиты считают первые письма за сутки — тем же счётом, что фильтр отправки
 * (`backend/features/outreach/limits.py`); у ящика `sent_today` — все письма, как было.
 * Своим файлом, а не в `types.ts`: тот у потолка длины.
 */

import { request } from './client';
import type { Stage } from './stages';
import type { SenderCard, SendersView } from './types';

/** Ящик с направлением: у этапов домены отправки свои. */
export interface StagedSender extends SenderCard {
  stage: Stage;
}

/** Домен рассылки строкой лимитов: лимит, выдержка, пауза и первые письма за сутки. */
export interface DomainLimit {
  domain: string;
  stage: Stage;
  daily_limit: number;
  sent_today: number;
  young_until: string | null;
  paused_at: string | null;
  pause_reason: string | null;
}

/** Направление целиком: дневной лимит (`null` — своего нет) и первые письма за сутки. */
export interface DirectionLimit {
  stage: Stage;
  daily_limit: number | null;
  sent_today: number;
}

export interface SendersByStage extends SendersView {
  senders: StagedSender[];
  domains: DomainLimit[];
  directions: DirectionLimit[];
}

/** Порядок разделов экрана и их заголовки. */
export const STAGE_TITLES: Record<Stage, string> = {
  donors: 'Доноры',
  advertisers: 'Рекламодатели',
  sales: 'Продажи',
};

export function listSendersByStage(): Promise<SendersByStage> {
  return request<SendersByStage>('/senders');
}
