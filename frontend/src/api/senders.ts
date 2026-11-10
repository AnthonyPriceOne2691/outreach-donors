/**
 * Домены рассылки по этапу (Ф4, 4.5a): лимиты и ящик (`sent_today`) считают первые письма
 * за сутки — тем же счётом, что кап, разгон и фильтр отправки; у добивок свой часовой потолок.
 * Своим файлом: `types.ts` у потолка длины.
 */

import type { ForecastSender, MailingForecastView } from '../sales/mailingTypes';
import { request } from './client';
import type { Stage } from './stages';
import type { SenderCard, SendersView } from './types';

export type { ForecastSender, MailingForecastView } from '../sales/mailingTypes';

/** Ящик этапа на экране доменов: почему не берёт писем — ещё и коротко, значком строки
 *  (`outreach/limits.short`); слова целиком — `refused`, по нажатию. */
export interface StageBox extends ForecastSender {
  refused_short: string | null;
}

/** Прогноз этапа глазами экрана доменов: из него нужны только ящики. */
export interface StageBoxesView extends MailingForecastView {
  senders: StageBox[];
}

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

/** `daily_limit: null` — своего лимита у направления нет. `unread` — имя настройки лимита,
 *  если она не прочиталась (опечатка в .env): лимит тогда ноль, пока её не поправят. */
export interface DirectionLimit {
  stage: Stage;
  daily_limit: number | null;
  sent_today: number;
  unread: string | null;
}

export interface SendersByStage extends SendersView {
  senders: StagedSender[];
  domains: DomainLimit[];
  directions: DirectionLimit[];
}

/** Полное имя части экрана: заголовок, имя пункта меню для диктора и подсказка свёрнутого меню.
 *  Подпись пункта одна — «Домены рассылки»: чьи они, говорит группа меню. */
export const SENDERS_TITLES: Record<Stage, string> = {
  donors: 'Домены рассылки доноров',
  advertisers: 'Домены рассылки рекламодателей',
  sales: 'Домены рассылки продаж',
};

/** Порядок разделов экрана и их заголовки. */
export const STAGE_TITLES: Record<Stage, string> = {
  donors: 'Доноры',
  advertisers: 'Рекламодатели',
  sales: 'Продажи',
};

export function listSendersByStage(): Promise<SendersByStage> {
  return request<SendersByStage>('/senders');
}

/** Ящики этапа со ступенями разгона — из общего прогноза почты. Плана и факта блоку не нужно. */
export function fetchStageBoxes(stage: Stage): Promise<StageBoxesView> {
  return request<StageBoxesView>(`/letters/forecast?stage=${stage}&days=1&back=0`);
}
