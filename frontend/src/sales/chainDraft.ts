/**
 * Шаг цепочки в окне правки: черновик, отказ до нажатия, тела запросов.
 *
 * **Границы — те, что назвал сервер** (`limits` в ответе набора). До нажатия экран
 * ловит только пустое и длинное: «Re:» в теме, незнакомую подстановку, метрики и
 * устройство зон судит сервер — теми же правилами, что файл из консоли, — и его
 * отказ показывается словами. Второй набор правил здесь разошёлся бы с ним.
 *
 * **Тема — только у первого письма**: у добивки поля темы нет, и в тело запроса
 * она уходит `null` — добивка идёт в той же переписке.
 */

import type { ChainPreviewBody, ChainStepBody, ChainStepCard, ChainView } from '../api/salesTypes';
import { formatNumber } from '../format';

export interface ChainDraft {
  subject: string;
  body: string;
  active: boolean;
}

/** Где шаг: набор, шаг и язык — ключ шаблона. */
export interface StepPlace {
  hypothesisId: number | null;
  step: number;
  language: string;
}

export const FIRST_STEP = 1;

/** Черновик: правка — от шаблона, новый шаг — пустой и включённый. */
export function draftOf(row: ChainStepCard | null): ChainDraft {
  if (row === null) return { subject: '', body: '', active: true };
  return { subject: row.subject ?? '', body: row.body, active: row.active };
}

function tooLong(value: string, limit: number): string | null {
  const length = value.trim().length;
  return length > limit
    ? `Длиннее ${formatNumber(limit)} знаков: сейчас ${formatNumber(length)}`
    : null;
}

/** Что не так с полями — словами, до отправки. Пусто — черновик можно слать. */
export function refusalsOf(
  draft: ChainDraft,
  step: number,
  limits: ChainView['limits'],
): Partial<Record<'subject' | 'body', string>> {
  const subject =
    step !== FIRST_STEP
      ? null
      : draft.subject.trim() === ''
        ? 'Впишите тему — у первого письма она обязательна'
        : tooLong(draft.subject, limits.subject);
  const body =
    draft.body.trim() === ''
      ? 'Впишите текст: зоны «[имя] rewrite» и «[имя] fixed»'
      : tooLong(draft.body, limits.body);
  const found = Object.entries({ subject, body }).filter(([, words]) => words !== null);
  return Object.fromEntries(found);
}

/** Изменилось ли что-нибудь против шаблона: без правки «Сохранить» не нужна. */
export function changed(draft: ChainDraft, row: ChainStepCard | null): boolean {
  return row === null || JSON.stringify(draft) !== JSON.stringify(draftOf(row));
}

export function previewBodyOf(draft: ChainDraft, place: StepPlace): ChainPreviewBody {
  return {
    step: place.step,
    language: place.language,
    subject: place.step === FIRST_STEP ? draft.subject : null,
    body: draft.body,
  };
}

export function stepBodyOf(draft: ChainDraft, place: StepPlace): ChainStepBody {
  return {
    ...previewBodyOf(draft, place),
    hypothesis_id: place.hypothesisId,
    active: draft.active,
  };
}
