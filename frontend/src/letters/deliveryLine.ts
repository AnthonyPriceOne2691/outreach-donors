/**
 * Доставка письма словами — одной строкой под письмом (29.10.2026).
 *
 * До этого письмо, которое сервер получателя не принимал, было просто «отправлено»: платформа
 * присылала отсрочку за отсрочкой, а мы их отбрасывали. Теперь вид причины и слова приходят
 * с сервера (`letters/deferrals.py`), а экран ставит перед ними, что с письмом, и добавляет
 * попытки и часы — по часам браузера:
 *
 * - ушло и откладывается — «Доставка откладывается: сервер получателя не отвечает (порт 25,
 *   тайм-аут) · 14 попыток с 10:26, последняя 16:22»;
 * - не доставлено — «Не доставлено: ящика получателя нет или он отключён (5.1.1)»; после
 *   отсрочек — ещё «· до этого откладывалась: 14 попыток с …»: и когда платформа сдалась, и когда
 *   сервер отказал прямо;
 * - доставлено после отсрочек — «До доставки откладывалась: … · 3 попытки с …».
 *
 * Чистыми функциями, без отрисовки: строка одна у карточки переписки и у экрана писем, и
 * проверяется тестом без браузера.
 */

import type { LetterDeferral, LetterDelivery } from '../api/delivery';
import type { MessageStatus } from '../api/types';
import { plural } from '../format';

const LOCALE = 'ru-RU';

/** Что с доставкой: ждёт исхода, не дошло или дошло не сразу. */
export type DeliveryTone = 'waiting' | 'refused' | 'delivered';

export interface DeliveryLine {
  tone: DeliveryTone;
  text: string;
}

/** Письмо — то, что строка читает: статус и что почта сказала о доставке. */
export interface DeliveryFacts extends LetterDelivery {
  status: MessageStatus;
}

/** Часы: сегодняшние — «10:26», другого дня — «28.10, 10:26». */
export function clockOf(moment: string, now: Date = new Date()): string {
  const at = new Date(moment);
  const time = at.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
  if (at.toDateString() === now.toDateString()) return time;
  return `${at.toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit' })}, ${time}`;
}

/** «14 попыток с 10:26, последняя 16:22»; одна — «попытка в 10:26». */
export function attemptsLine(deferral: LetterDeferral, now: Date = new Date()): string {
  const since = clockOf(deferral.since, now);
  if (deferral.attempts <= 1) return `попытка в ${since}`;
  const count = `${deferral.attempts} ${plural(deferral.attempts, 'попытка', 'попытки', 'попыток')}`;
  return `${count} с ${since}, последняя ${clockOf(deferral.last, now)}`;
}

/** Строка доставки письма; сказать нечего — `null`: письмо в очереди, дошло сразу или сервер
 *  до выкатки полей не отдавал. */
export function deliveryLine(letter: DeliveryFacts, now: Date = new Date()): DeliveryLine | null {
  const deferral = letter.deferral ?? null;
  if (letter.status === 'bounced' && letter.refusal) {
    const before =
      deferral === null ? '' : ` · до этого откладывалась: ${attemptsLine(deferral, now)}`;
    return { tone: 'refused', text: `Не доставлено: ${letter.refusal}${before}` };
  }
  if (deferral === null) return null;
  const tries = attemptsLine(deferral, now);
  if (letter.status === 'sent') {
    return { tone: 'waiting', text: `Доставка откладывается: ${deferral.words} · ${tries}` };
  }
  if (letter.status === 'delivered') {
    return { tone: 'delivered', text: `До доставки откладывалась: ${deferral.words} · ${tries}` };
  }
  return null;
}
