/**
 * Что отдают две ручки вкладки «Рассылка»: части адресов продаж (`GET /sales/mailing`) и прогноз
 * рассылки этапа (`GET /letters/forecast`). Плоско, как у сервера: правила за числами живут там
 * (`features/sales/mailing.py`, `features/letters/forecast.py`), а экран их только называет.
 *
 * Своим файлом рядом с вкладкой, а не в `api/salesTypes.ts`: тот общий у всех вкладок продаж и
 * у потолка длины, а прогноз — ответ общей ручки почты, которую вкладка читает с этапом продаж.
 */

import type { SalesAutoLine } from '../api/salesTypes';
import type { Stage } from '../api/stages';

/** Причина словами и сколько адресов по ней — старшие по числу первыми. */
export interface MailingWhy {
  words: string;
  count: number;
}

/** Части адресов: каждый лид — ровно в одной, и `total` — их сумма, построением сервера. */
export interface MailingParts {
  total: number;
  /** Первое письмо ушло (дошло, вернулось или в полёте). */
  sent: number;
  /** Первое письмо ждёт в очереди — тем же счётом, что пачка и прогноз (`queued_first`). */
  queued: number;
  /** Без письма, и сборка напишет — её же решением (`supply_extra` прогноза). */
  will_write: number;
  /** Без письма, а сборка ждёт — почему, словами решения (`wont_write_why`). */
  wont_write: number;
  /** Лид на очистке. */
  not_ready: number;
  /** Лид отклонён или его первое письмо снято и заново не пишется (`removed_why`). */
  removed: number;
  wont_write_why: MailingWhy[];
  removed_why: MailingWhy[];
}

export interface MailingRow {
  hypothesis_id: number;
  name: string;
  parts: MailingParts;
}

/** Ручка частей адресов: строки автоотправки и автосборки — те же, что у «Очереди писем»; по
 *  гипотезам (и без лидов — с нулями) и итог. */
export interface SalesMailingView {
  autosend: SalesAutoLine;
  autobuild: SalesAutoLine;
  rows: MailingRow[];
  total: MailingParts;
}

/** Темп автоотправки: пауза между первыми письмами ящика и её разброс долей. */
export interface ForecastPace {
  gap_minutes: number;
  spread: number;
}

/** Сегодня у этапа: ушло, мощность дня и сколько ещё пустят лимиты ящиков. */
export interface ForecastToday {
  sent_first: number;
  sent_followups: number;
  /** Мощность дня целиком — с лимитами, сниженными мягкими отказами. */
  allowance: number;
  /** Сколько первых писем ещё пустит выбор ящика — без окна получателя и темпа. */
  remaining_first: number;
  /** У каких ящиков лимит снижен на сутки — словами; пусто — ни у каких. */
  cut: string | null;
}

/** День плана: сегодня (первый) и дни вперёд — дни по UTC, `YYYY-MM-DD`. */
export interface ForecastDay {
  day: string;
  sending_day: boolean;
  /** Почему писем нет или меньше мощности — словами; пусто — уходят во всю мощность. */
  why_not: string | null;
  /** Мощность ящиков с разгоном; в день без окна — ноль. Сегодня — мощность дня целиком. */
  capacity_first: number;
  /** Сколько первых писем уйдёт — не больше, чем есть; сегодня — из остатка дня. */
  planned_first: number;
  /** Добивок со сроком в этот день; срок в день без окна — в следующем дне отправки. */
  followups_due: number;
}

export interface ForecastStuckReason {
  words: string;
  letters: number;
}

/** Письма очереди, которые не уйдут, пока их не поправят: срок и план — без них. */
export interface ForecastStuck {
  letters: number;
  reasons: ForecastStuckReason[];
}

/** День назад: сколько ушло на самом деле. Старые первыми, без сегодня. */
export interface ForecastPastDay {
  day: string;
  sent_first: number;
  sent_followups: number;
}

/** Когда уйдёт последнее письмо очереди; `words` есть всегда — как посчитано или почему срока нет. */
export interface ForecastEta {
  day: string | null;
  /** Дней отправки, считая сегодня, если сегодня письма ещё уходят. */
  sending_days: number | null;
  /** Часов от сейчас — только при автоотправке. */
  hours: number | null;
  words: string;
}

/** Ящик этапа: ступень разгона сегодня и следующая. */
export interface ForecastSender {
  id: number;
  email: string;
  domain: string;
  enabled: boolean;
  /** День разгона; 0 — ящик без разгона. */
  warmup_day: number;
  warmup_finished: boolean;
  /** Первое письмо разгона; пусто — ещё не писал, ступень пойдёт с первого письма. */
  first_letter_at: string | null;
  daily_cap: number;
  /** Потолок сегодня: ступень разгона, при мягких отказах — сниженный на сутки лимит. */
  allowance_today: number;
  sent_today: number;
  cut: boolean;
  /** Почему первых писем ящик сегодня не берёт — словами фильтра лимитов. */
  refused: string | null;
  next_step_on: string | null;
  next_step_allowance: number | null;
}

/** Прогноз рассылки этапа целиком (`MailingForecastView` сервера). */
export interface MailingForecastView {
  stage: Stage;
  now: string;
  queued_first: number;
  queued_stuck: ForecastStuck | null;
  supply_extra: number | null;
  /** Окно получателя по его часам, «пн–пт 09:00–17:00»; пусто — в любой час. */
  window: string | null;
  /** Пояс, в котором посчитаны часы окна, — самый частый у получателей очереди. */
  zone: string | null;
  /** Темп автоотправки; пусто — вручную, пачкой по нажатию. */
  pace: ForecastPace | null;
  /** Почему первые письма этапа сейчас не уходят вовсе — словами. */
  blocked: string | null;
  /** Лимит направления не прочитан из настроек — что поправить. */
  unread_limit: string | null;
  today: ForecastToday;
  days: ForecastDay[];
  past: ForecastPastDay[];
  queue_eta: ForecastEta;
  senders: ForecastSender[];
}
