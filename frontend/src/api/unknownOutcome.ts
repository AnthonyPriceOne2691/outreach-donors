/** Исход неизвестен: письмо «отправляется», а ушло ли оно — неизвестно
 *  (`letters/unknown_outcome.py`). Связь с почтой оборвалась посреди передачи,
 *  и платформа о письме не сообщила; решает человек по её журналу.
 *  Отдельным файлом: `types.ts` упёрся в свой потолок. */

import type { LetterStage, MessageStatus } from './types';

/** Письмо с неизвестным исходом — с тем, по чему его ищут в журнале платформы. */
export interface UnknownLetter {
  id: number;
  host: string;
  /** Кому ушло: у ответа в переписке — тому, кто ответил. */
  email: string | null;
  /** С какого ящика. */
  sender_email: string | null;
  campaign: string;
  /** Какое это письмо словами: «первое письмо», «добивка 1», «ответ в переписке». */
  what: string;
  /** Когда письмо отдали почте. Если оно ушло, от этого времени идут сроки добивок. */
  since: string;
  /** Переписка: добивку и ответ после решения ведёт она, а не очередь. */
  thread_id: number | null;
}

export interface UnknownLettersView {
  stage: LetterStage;
  letters: UnknownLetter[];
  /** Через сколько минут после начала передачи письмо попадает в список. */
  after_minutes: number;
}

/** Что человек нашёл в журнале платформы: письмо ушло или его там нет. */
export type ResolveOutcome = 'sent' | 'queued';

/** Чем кончилось решение: состояние письма и то же словами сервера. */
export interface ResolvedLetter {
  id: number;
  status: MessageStatus;
  said: string;
}
