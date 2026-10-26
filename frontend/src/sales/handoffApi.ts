/**
 * Передача лида продаж телемаркетологу: кнопка `POST /api/sales/threads/{N}/handoff` и поле
 * `handoff` выдачи переписки (`GET /api/threads/{N}`). Типы — здесь, у компонента: `types.ts`
 * и `thread.ts` — общие файлы «Диалогов».
 */

import { request } from '../api/client';

/** Что с Kommo: ждёт задачи, повтор, не подтверждено, отказ, сделка есть, Kommo не подключён. */
export type HandoffKommo = 'pending' | 'retry' | 'unconfirmed' | 'failed' | 'done' | 'off';
/** Сообщение телемаркетологу: ещё не отправлено, отправлено, не доставлено. */
export type HandoffTelegram = 'pending' | 'sent' | 'undelivered';

/** Передача, как её знает сервер сейчас. Слова — сервера: экран их не пересказывает. */
export interface SalesHandoff {
  id: number;
  /** Когда лид передан. */
  at: string;
  kommo: HandoffKommo;
  kommo_words: string;
  telegram: HandoffTelegram;
  telegram_words: string;
}

/** Итог нажатия «Передать телемаркетологу». */
export interface HandedOver {
  handoff: SalesHandoff;
  /** Задача встала в очередь сейчас; нет — её возьмёт проход по расписанию. */
  queued: boolean;
  /** Ответы переписки, снятые с ожидания человека. */
  released: number[];
  /** Итог словами — под кнопкой и в уведомлении. */
  said: string;
}

/** Что переписка (`GET /api/threads/{id}`) знает о передаче лида — сверх `ThreadView`. */
export interface ThreadHandoff {
  handoff?: SalesHandoff | null;
}

export function handOver(threadId: number): Promise<HandedOver> {
  return request<HandedOver>(`/sales/threads/${threadId}/handoff`, { method: 'POST' });
}
