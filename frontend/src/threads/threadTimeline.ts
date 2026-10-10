/**
 * Лента переписки без отрисовки: порядок писем и ответов, какой ответ разбирать
 * под лентой и на какой отвечает форма ответа.
 *
 * Чистыми функциями, а не внутри экрана: правило «что под лентой» — одно на экран,
 * и проверяется оно тестом без браузера.
 */

import type { IncomingCard, LetterCard } from '../api/types';

export type FeedItem =
  | { kind: 'letter'; at: number; letter: LetterCard }
  | { kind: 'reply'; at: number; reply: IncomingCard };

/** Письмо без времени отправки ещё не ушло — его место в конце ленты. Пустая строка
 *  сортировалась первой, и ответ, стоящий в очереди, встал бы над первым письмом. */
function sentAt(letter: LetterCard): number {
  return letter.sent_at === null ? Number.POSITIVE_INFINITY : Date.parse(letter.sent_at);
}

/** Письма и ответы одной лентой по времени — как их читал бы человек в почте. */
export function feedOf(letters: LetterCard[], incoming: IncomingCard[]): FeedItem[] {
  const items: FeedItem[] = [
    ...letters.map((letter) => ({ kind: 'letter' as const, at: sentAt(letter), letter })),
    ...incoming.map((reply) => ({
      kind: 'reply' as const,
      at: Date.parse(reply.received_at),
      reply,
    })),
  ];
  return items.sort((a, b) => a.at - b.at);
}

/** Ответы по времени прихода: сервер отдаёт их в своём порядке. */
function byTime(incoming: IncomingCard[]): IncomingCard[] {
  return [...incoming].sort((a, b) => Date.parse(a.received_at) - Date.parse(b.received_at));
}

/** Ответ ждёт решения человека: цену подтвердить, лид взять или ответ продаж разобрать.
 *  Ждёт ли разбор — решает сервер (`needs_review`) тем же правилом, что числа меню:
 *  ответ, перекрытый более поздним с принятой ценой, у него не ждёт (`superseded_by`),
 *  и своей копии этого правила у экрана нет (проверка прода 10.10.2026). */
export function waitsForPerson(reply: IncomingCard): boolean {
  return reply.reviewed_at === null && (reply.needs_review || reply.lead);
}

/**
 * Какой ответ разбирать под лентой: выбранный человеком кнопкой в пузыре — или
 * последний из ждущих. Не ждёт никто и никто не выбран — под лентой разбора нет:
 * форма цены у каждого ответа и растягивала переписку в простыню.
 */
export function activeReply(incoming: IncomingCard[], picked: number | null): IncomingCard | null {
  const chosen = incoming.find((reply) => reply.id === picked);
  return chosen ?? byTime(incoming).filter(waitsForPerson).at(-1) ?? null;
}

/** На что отвечает форма ответа: последний ответ человека. Автоответчику, отказу
 *  доставки и отписке не отвечают. */
export function answerTarget(incoming: IncomingCard[]): IncomingCard | null {
  return (
    byTime(incoming)
      .filter((reply) => reply.kind === 'human')
      .at(-1) ?? null
  );
}

/** Что показать в пузыре ответа: написанное человеком, а письмо целиком — по раскрытию. */
export function freshOf(reply: IncomingCard): { text: string; cut: boolean } {
  const fresh = (reply.fresh_body ?? reply.raw_body).trim();
  return { text: fresh, cut: fresh !== reply.raw_body.trim() };
}
