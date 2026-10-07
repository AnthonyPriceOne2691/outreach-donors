/**
 * Плитка «Почта» на экране писем — словами (решение владельца 07.10.2026).
 *
 * Сервер называет транспорт кодом (`letters/transport_factory.py`), и до
 * 07.10 в плитке так и стояло «sendgrid». По коду транспорт ищут в журнале;
 * человеку нужно имя платформы и одно — уходят ли письма.
 */

import type { LetterTransport } from '../api/types';

/** Платформы отправки по имени. Незнакомая настоящая — общими словами,
 *  а не кодом: сервер завёл новую, а экран о ней ещё не знает. */
const PLATFORMS: Record<string, string> = { sendgrid: 'SendGrid' };

export interface MailTile {
  value: string;
  hint: string;
}

export function mailTile(transport: LetterTransport): MailTile {
  if (transport.real) {
    return { value: PLATFORMS[transport.name] ?? 'подключена', hint: 'письма уходят' };
  }
  // Настоящая почта выбрана, но не собралась — это не проверочная: причина
  // стоит плашкой «Почта не подключилась» ниже, и подпись ей не перечит.
  if (transport.problem !== null) return { value: 'не подключена', hint: 'письма не уходят' };
  return { value: 'не подключена', hint: 'проверочная почта, письма не уходят' };
}
