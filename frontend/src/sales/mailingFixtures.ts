/**
 * Ответы двух ручек вкладки «Рассылка» для тестов: части адресов продаж (`GET /sales/mailing`) и
 * прогноз рассылки этапа продаж (`GET /letters/forecast`). Гипотезы, ящики и домены выдуманы, ниши и
 * языки — разные; числа согласованы, как их согласует сервер: части складываются во «всего»,
 * «в очереди» итога = `queued_first`, «напишет» итога = `supply_extra`.
 *
 * «Сейчас» — понедельник 02.11.2026, 10:00 UTC: окно получателя пн–пт, в субботу и воскресенье
 * писем нет; очередь кончается в среду.
 */

import type { MailingForecastView, MailingParts, SalesMailingView } from './mailingTypes';

export const MAILING_NOW = new Date('2026-11-02T10:00:00Z');

export const FORECAST_ROUTE = 'GET /api/letters/forecast?stage=sales&days=7&back=7';
export const MAILING_ROUTE = 'GET /api/sales/mailing';

/** Слова срока сервера при автоотправке (`forecast_plan._how`). */
export const AUTO_WORDS =
  'последнее письмо очереди уйдёт ср 04.11, дней отправки: 3 (без 3 писем, которые не уйдут, ' +
  'пока их не поправят), ≈ через 54.3 ч: автоотправка, письмо с ящика раз в 15 мин (±50%), окно — ' +
  'по часам America/Toronto';

/** Слова срока сервера вручную: срок верен, если жать пачку каждый день отправки, часов нет. */
export const MANUAL_WORDS =
  'последнее письмо очереди уйдёт ср 04.11, дней отправки: 3 (без 3 писем, которые не уйдут, ' +
  'пока их не поправят) — вручную: пачка выбирает дневной лимит за секунды (до 200 писем за ' +
  'нажатие), срок верен, если нажимать «Отправить очередь» каждый день отправки в часы окна ' +
  'получателя; часов здесь нет';

export const CUT_WORDS =
  'мягкие отказы: лимит на сутки снижен вдвое у hello@mail-b.example.test — сегодняшняя мощность ' +
  'считает сниженный, как выбор ящика';

const WEEKEND = 'не день отправки: окно получателя — пн–пт 09:00–17:00 по его часам';
export const NOTHING_LEFT =
  'отправлять больше нечего: кончились и очередь, и лиды, которым сборка напишет';

export const FORECAST: MailingForecastView = {
  stage: 'sales',
  now: MAILING_NOW.toISOString(),
  queued_first: 96,
  queued_stuck: {
    letters: 3,
    reasons: [
      { words: 'адрес лида ждёт проверки', letters: 2 },
      { words: 'пояс получателя неизвестен', letters: 1 },
    ],
  },
  supply_extra: 40,
  window: 'пн–пт 09:00–17:00',
  zone: 'America/Toronto',
  pace: { gap_minutes: 15, spread: 0.5 },
  blocked: null,
  unread_limit: null,
  today: {
    sent_first: 6,
    sent_followups: 2,
    allowance: 30,
    remaining_first: 24,
    cut: CUT_WORDS,
  },
  days: [
    {
      day: '2026-11-02',
      sending_day: true,
      why_not: 'сегодня уже ушло 6 первых писем — остаток дня 24',
      capacity_first: 30,
      planned_first: 24,
      followups_due: 5,
    },
    {
      day: '2026-11-03',
      sending_day: true,
      why_not: null,
      capacity_first: 35,
      planned_first: 35,
      followups_due: 3,
    },
    {
      day: '2026-11-04',
      sending_day: true,
      why_not: null,
      capacity_first: 35,
      planned_first: 35,
      followups_due: 0,
    },
    {
      day: '2026-11-05',
      sending_day: true,
      why_not: NOTHING_LEFT,
      capacity_first: 40,
      planned_first: 39,
      followups_due: 4,
    },
    {
      day: '2026-11-06',
      sending_day: true,
      why_not: NOTHING_LEFT,
      capacity_first: 40,
      planned_first: 0,
      followups_due: 2,
    },
    {
      day: '2026-11-07',
      sending_day: false,
      why_not: WEEKEND,
      capacity_first: 0,
      planned_first: 0,
      followups_due: 0,
    },
    {
      day: '2026-11-08',
      sending_day: false,
      why_not: WEEKEND,
      capacity_first: 0,
      planned_first: 0,
      followups_due: 0,
    },
  ],
  past: [
    { day: '2026-10-26', sent_first: 20, sent_followups: 4 },
    { day: '2026-10-27', sent_first: 22, sent_followups: 6 },
    { day: '2026-10-28', sent_first: 25, sent_followups: 3 },
    { day: '2026-10-29', sent_first: 28, sent_followups: 8 },
    { day: '2026-10-30', sent_first: 30, sent_followups: 5 },
    { day: '2026-10-31', sent_first: 0, sent_followups: 0 },
    { day: '2026-11-01', sent_first: 0, sent_followups: 0 },
  ],
  queue_eta: { day: '2026-11-04', sending_days: 3, hours: 54.3, words: AUTO_WORDS },
  senders: [
    {
      id: 1,
      email: 'hello@mail-a.example.test',
      domain: 'mail-a.example.test',
      enabled: true,
      warmup_day: 40,
      warmup_finished: true,
      first_letter_at: '2026-09-24T09:00:00Z',
      daily_cap: 20,
      allowance_today: 20,
      sent_today: 4,
      cut: false,
      refused: null,
      next_step_on: null,
      next_step_allowance: null,
    },
    {
      id: 2,
      email: 'hello@mail-b.example.test',
      domain: 'mail-b.example.test',
      enabled: true,
      warmup_day: 9,
      warmup_finished: false,
      first_letter_at: '2026-10-25T14:00:00Z',
      daily_cap: 20,
      allowance_today: 5,
      sent_today: 2,
      cut: true,
      refused: null,
      next_step_on: '2026-11-05',
      next_step_allowance: 15,
    },
    {
      id: 3,
      email: 'team@mail-c.example.test',
      domain: 'mail-c.example.test',
      enabled: true,
      warmup_day: 1,
      warmup_finished: false,
      first_letter_at: null,
      daily_cap: 20,
      allowance_today: 5,
      sent_today: 0,
      cut: false,
      refused: null,
      next_step_on: '2026-11-05',
      next_step_allowance: 10,
    },
    {
      id: 4,
      email: 'office@mail-d.example.test',
      domain: 'mail-d.example.test',
      enabled: true,
      warmup_day: 3,
      warmup_finished: false,
      first_letter_at: '2026-10-31T15:00:00Z',
      daily_cap: 20,
      allowance_today: 5,
      sent_today: 0,
      cut: false,
      refused: 'домен на паузе до 06.11',
      next_step_on: '2026-11-08',
      next_step_allowance: 10,
    },
    {
      id: 5,
      email: 'sales@mail-e.example.test',
      domain: 'mail-e.example.test',
      enabled: false,
      warmup_day: 12,
      warmup_finished: false,
      first_letter_at: '2026-10-22T10:00:00Z',
      daily_cap: 20,
      allowance_today: 15,
      sent_today: 0,
      cut: false,
      refused: null,
      next_step_on: '2026-11-04',
      next_step_allowance: 20,
    },
  ],
};

/** Вручную: темпа нет, часов нет — срок словами сервера с условием пачки. */
export const FORECAST_MANUAL: MailingForecastView = {
  ...FORECAST,
  pace: null,
  queue_eta: { day: '2026-11-04', sending_days: 3, hours: null, words: MANUAL_WORDS },
};

export const BLOCKED =
  'Первые письма этапа не уходят: продажи к почте ещё не подключены — не задан физический адрес ' +
  '— вкладка «Отправитель»';

/** Продажи к почте не подключены: письма не уходят вовсе, срока нет — почему, словами сервера. */
export const FORECAST_BLOCKED: MailingForecastView = {
  ...FORECAST,
  blocked: BLOCKED,
  queue_eta: { day: null, sending_days: null, hours: null, words: `очередь не уходит: ${BLOCKED}` },
};

export const AUTOSEND_ON =
  'Автоотправка включена, список разрешённых адресатов почты продаж пуст — письма уходят всем ' +
  'лидам очереди: проход раз в 60 с, с ящика — не больше письма за проход и пауза 8–22 мин ' +
  'между его первыми письмами. В окне получателя сейчас 12 писем. Письма уходят.';
export const AUTOSEND_OFF =
  'Автоотправка выключена — первые письма уходят кнопкой «Отправить очередь» или по одному; ' +
  'включается настройкой SALES_AUTOSEND.';
export const AUTOBUILD_OFF =
  'Автосборка выключена — очередь собирают кнопкой «Собрать очередь»; включается настройкой ' +
  'SALES_AUTOBUILD.';

const CAFES: MailingParts = {
  total: 180,
  sent: 70,
  queued: 60,
  will_write: 25,
  wont_write: 10,
  not_ready: 5,
  removed: 10,
  wont_write_why: [{ words: 'цепочка на португальском задана не целиком', count: 10 }],
  removed_why: [
    { words: 'дубль', count: 6 },
    { words: 'домен не принимает почту', count: 4 },
  ],
};

const DENTISTS: MailingParts = {
  total: 120,
  sent: 40,
  queued: 36,
  will_write: 15,
  wont_write: 8,
  not_ready: 21,
  removed: 0,
  wont_write_why: [{ words: 'у лида нет значения для имени', count: 8 }],
  removed_why: [],
};

export const MAILING: SalesMailingView = {
  autosend: { enabled: true, line: AUTOSEND_ON },
  autobuild: { enabled: false, line: AUTOBUILD_OFF },
  rows: [
    { hypothesis_id: 5, name: 'Кофейни Лиссабона', parts: CAFES },
    { hypothesis_id: 8, name: 'Dental clinics — Toronto', parts: DENTISTS },
  ],
  total: {
    total: 300,
    sent: 110,
    queued: 96,
    will_write: 40,
    wont_write: 18,
    not_ready: 26,
    removed: 10,
    wont_write_why: [
      { words: 'цепочка на португальском задана не целиком', count: 10 },
      { words: 'у лида нет значения для имени', count: 8 },
    ],
    removed_why: CAFES.removed_why,
  },
};

/** Вручную: автоотправка выключена — строкой сервера. */
export const MAILING_MANUAL: SalesMailingView = {
  ...MAILING,
  autosend: { enabled: false, line: AUTOSEND_OFF },
};

/** Причин снятых писем больше пяти: слова платформы у каждого письма свои (выдуманные). */
export const MANY_REMOVED = [
  { words: 'дубль', count: 4 },
  { words: 'отписка', count: 2 },
  { words: 'домен не принимает почту', count: 1 },
  { words: 'письмо снято: 550 выдуманный ящик не найден', count: 1 },
  { words: 'письмо снято: 552 выдуманный ящик переполнен', count: 1 },
  { words: 'письмо снято: 554 выдуманный отказ получателя', count: 1 },
  { words: 'письмо снято: 421 выдуманная временная ошибка', count: 1 },
];

/** Итог с длинным списком причин «сняты»: части по-прежнему складываются во «всего». */
export const MAILING_MANY_REASONS: SalesMailingView = {
  ...MAILING,
  total: { ...MAILING.total, total: 301, removed: 11, removed_why: MANY_REMOVED },
};
