/**
 * Исход поиска адреса — значения сервера (`ContactStatus`) и их слова.
 *
 * Своим файлом с 20.10.2026: `types.ts` и `labels.ts` — у потолка длины, а исход
 * пополнился «не спросили» (`paid_off`, проход без платной ступени). Оба файла отдают
 * отсюда те же имена, что раньше, и импорты остальных модулей не менялись. Слова — те
 * же, что у сервера (`donors/wording.py`): сверку держит тест сервера.
 */

export type ContactStatus =
  | 'found'
  | 'not_found'
  | 'form_only'
  | 'no_quota'
  | 'rate_limited'
  | 'blocked'
  | 'error'
  | 'no_answer'
  | 'paid_off';

export const CONTACT_STATUSES: Record<ContactStatus, { title: string; color: string }> = {
  found: { title: 'адрес найден', color: 'green' },
  not_found: { title: 'адреса нет', color: 'gray' },
  form_only: { title: 'только форма', color: 'yellow' },
  no_quota: { title: 'кончилась квота', color: 'yellow' },
  rate_limited: { title: 'предел запросов', color: 'yellow' },
  // Красный, а не жёлтый: жёлтое оператор ждёт, а здесь ждать нечего —
  // пока учётку не откроют, платная ступень мертва по всем доменам.
  blocked: { title: 'учётка закрыта', color: 'red' },
  error: { title: 'ошибка поиска', color: 'red' },
  // Жёлтый: сайт не ответил, повтор идёт сам по сроку — человеку ждать.
  no_answer: { title: 'сайт не ответил', color: 'yellow' },
  // Серый, как «адреса нет»: общая очередь держит его тем же сроком.
  paid_off: { title: 'платный поиск выключен — не спросили', color: 'gray' },
};

/** Имя исхода для значка постоянной ширины (колонка «Адреса» доноров) — где полное
 *  не помещается; полное тогда — подсказкой у значка. */
export const CONTACT_STATUS_SHORT: Partial<Record<ContactStatus, string>> = {
  paid_off: 'не спросили',
};
