/** Отсрочка и отказ доставки письма — словами сервера (`letters/deferrals.py`, 29.10.2026).
 *  Вид причины и слова — с сервера: таблица образцов одна, и второй её копии у экрана нет.
 *  Отдельным файлом: `types.ts` упёрся в свой потолок. Поля карточки письма — необязательные:
 *  экран, открытый до выкатки, их не получит. */

import type { LetterStage } from './types';

/** Последняя отсрочка: платформа не отдала письмо серверу получателя и повторяет. */
export interface LetterDeferral {
  /** Вид причины: `unreachable`, `mailbox_full`, `try_later`, … */
  kind: string;
  /** Словами сервера, с коротким кодом в скобках: «сервер получателя не отвечает
   *  (порт 25, тайм-аут)»; вид не узнан — ответ почты, обрезанный. */
  words: string;
  /** Сколько попыток доставки — по счёту платформы. */
  attempts: number;
  /** Первая отсрочка — с какого часа платформа повторяет. */
  since: string;
  /** Последняя попытка. */
  last: string;
}

/** Что почта сказала о доставке письма сверх «ушло». */
export interface LetterDelivery {
  /** Последняя отсрочка; остаётся и после доставки или отказа. */
  deferral?: LetterDeferral | null;
  /** Почему не доставлено — словами; пусто — отказа нет. */
  refusal?: string | null;
}

/** Письмо, доставка которого откладывается, — блок на экране писем. */
export interface DeferredLetter {
  id: number;
  host: string;
  email: string | null;
  campaign: string;
  /** Переписка письма: там вся его история. */
  thread_id: number | null;
  deferral: LetterDeferral;
}

export interface DeferredLettersView {
  stage: LetterStage;
  /** Первые — дольше всех; сколько всего — `total`. */
  letters: DeferredLetter[];
  total: number;
}
