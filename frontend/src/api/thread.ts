/** Переписка целиком — ответ `GET /api/threads/{id}`.
 *  Отдельным файлом: `types.ts` упёрся в свой потолок. */

import type { FileRules, OutgoingFile } from './files';
import type { Corridor, IncomingCard, LetterCard, ThreadCard } from './types';

/** Ящик переписки, пишет ли он и срок следующей добивки (`letters/mailbox.py`). */
export interface ThreadMail {
  /** Адрес ящика переписки; пусто — ящик удалили. */
  mailbox: string | null;
  /** Почему письма переписки ждут — словами отказа отправки; пусто — ящик пишет. */
  waiting: string | null;
  /** Шаг и срок следующей добивки; пусто — добивки не будет. */
  next_step: number | null;
  next_at: string | null;
}

export interface ThreadView {
  card: ThreadCard;
  letters: LetterCard[];
  incoming: IncomingCard[];
  /** Коридор отличия — с сервера, как и на экране писем. */
  corridor: Corridor;
  /** Пусто — первое письмо ещё не уходило: ящик выберется при отправке. */
  mail: ThreadMail | null;
  /** Файлы, загруженные к ответу, но ещё не ушедшие: скрепка восстанавливает их
   *  после перезагрузки страницы. */
  pending_files?: OutgoingFile[];
  /** Правила файла к ответу — с сервера. */
  file_rules?: FileRules;
}
