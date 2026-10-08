/**
 * Домены рассылки по этапу (Ф4, 4.5a): лимиты и ящик (`sent_today`) считают первые письма
 * за сутки — тем же счётом, что кап, разгон и фильтр отправки; у добивок свой часовой потолок.
 * Своим файлом: `types.ts` у потолка длины.
 */

import { request } from './client';
import type { Stage } from './stages';
import type { SenderCard, SendersView } from './types';

export interface StagedSender extends SenderCard {
  stage: Stage;
}

export interface DomainLimit {
  domain: string;
  stage: Stage;
  daily_limit: number;
  sent_today: number;
  young_until: string | null;
  paused_at: string | null;
  pause_reason: string | null;
}

/** `daily_limit: null` — своего лимита у направления нет. */
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
