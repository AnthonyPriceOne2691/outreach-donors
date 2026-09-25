/**
 * Текст письма на экране: пометки незаданного и число отличия.
 *
 * **Громкая метка сервера — тихой пометкой, и в чтении, и в правке.** Метка
 * «ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО» громкая намеренно: по ней отправка отказывает,
 * и в тексте на сервере она остаётся. Но экран, где подпись кричит
 * заглавными, показывает поломку там, где просто ещё не подключили почту, —
 * и так было и во вкладках добивок, и в поле правки, пока тихой пометкой
 * становилось только первое письмо (замечание 25.09.2026).
 *
 * **Правка возвращает метки на место.** Человек правит текст с пометкой
 * «[имя отправителя]», а на сервер уходит громкая метка — та, по которой
 * отправка откажет. Уйди пометка как есть, сервер не узнал бы в ней
 * незаданное имя, и письмо ушло бы подписанным словами в скобках.
 * Поэтому ошибка возврата — всегда в сторону «не отправится».
 *
 * **Число отличия не спорит с коридором.** 0,254 целыми процентами — «25%
 * выше коридора 15–25%», фраза, опровергающая себя. Десятая — всегда, а у
 * самого края коридора, где и десятой мало, — сотые. То же правило у текста
 * вердикта на сервере (`letters/uniqueness.py`, `percent_text`).
 */

import type { Corridor } from '../api/types';
import { formatPercent } from '../format';

/** Метка незаданного значения, как её ставит сервер: «ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО». */
const UNSET_MARK = /«([А-ЯЁ ]+?) НЕ ЗАДАН[ОА]?»/g;

function soft(title: string): string {
  return `[${title.toLowerCase()}]`;
}

/** Текст для чтения и правки: громкие метки — тихой пометкой в скобках. */
export function readable(text: string | null | undefined): { text: string; unset: boolean } {
  let unset = false;
  const softened = (text ?? '').replace(UNSET_MARK, (_, title: string) => {
    unset = true;
    return soft(title);
  });
  return { text: softened, unset };
}

/**
 * Поправленный текст — обратно с громкими метками исходного.
 *
 * Возвращаются только метки, стоявшие в исходном тексте: пометку, которую
 * человек заменил настоящим значением, возвращать нечем и незачем.
 */
export function restored(edited: string, original: string | null | undefined): string {
  let text = edited;
  for (const mark of (original ?? '').match(UNSET_MARK) ?? []) {
    const title = mark.slice(1, -1).replace(/ НЕ ЗАДАН[ОА]?$/, '');
    text = text.split(soft(title)).join(mark);
  }
  return text;
}

/** Точнее сотых отличие не нужно: письмо — это сотни слов, не миллионы. */
const MAX_DIGITS = 3;

function rounded(percent: number, digits: number): number {
  const scale = 10 ** digits;
  return Math.round(percent * scale) / scale;
}

/** Отличие процентами: «25,4%», «19%», у края коридора — «25,04%». */
export function uniquenessText(share: number | null | undefined, corridor: Corridor): string {
  if (share === null || share === undefined) return '—';
  const percent = share * 100;
  for (let digits = 1; digits <= MAX_DIGITS; digits += 1) {
    const shown = rounded(percent, digits);
    const honest =
      share < corridor.min
        ? shown < corridor.min * 100
        : share > corridor.max
          ? shown > corridor.max * 100
          : true;
    if (honest) return formatPercent(share, digits);
  }
  return formatPercent(share, MAX_DIGITS);
}

/** Коридор словами: «15–25%». Границы — с сервера, как и сам коридор. */
export function corridorText(corridor: Corridor): string {
  return `${Math.round(corridor.min * 100)}–${Math.round(corridor.max * 100)}%`;
}
