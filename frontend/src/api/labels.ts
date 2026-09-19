/**
 * Русские подписи к тому, что сервер называет по-английски.
 *
 * Сервер отдаёт именованные действия (`view`, `run`, …) — так они
 * и лежат в базе, в журнале и в отказах, и переименовывать их нельзя:
 * по этим именам разбирают инциденты. А человеку в интерфейсе нужны
 * слова его языка, поэтому перевод живёт здесь, одним местом на весь
 * фронт: разъехавшиеся подписи одного и того же действия на двух
 * экранах читаются как два разных права.
 */

import type { MessageStatus, Permission, ReplyKind, Role, ThreadState } from './types';

export const PERMISSION_TITLES: Record<Permission, string> = {
  view: 'смотреть базу',
  run: 'запускать прогоны',
  settings: 'править пороги',
  send: 'отправлять письма',
  senders: 'домены рассылки',
  users: 'заводить учётки',
};

export const ROLE_TITLES: Record<Role, string> = {
  admin: 'админ',
  operator: 'оператор',
};

export function permissionTitle(permission: string): string {
  return PERMISSION_TITLES[permission as Permission] ?? permission;
}

/** Состояния диалога словами и цветом.
 *
 *  Цвет идёт от смысла: мята — дошло до цели, янтарь — нужно внимание,
 *  роза — тупик. Состояний семь, цветов четыре — и это намеренно:
 *  пятый цвет означал бы, что смысл потерян.
 */
export const THREAD_STATES: Record<ThreadState, { title: string; color: string }> = {
  queued: { title: 'в очереди', color: 'gray' },
  waiting: { title: 'ждём ответа', color: 'blue' },
  replied: { title: 'ответил человек', color: 'teal' },
  priced: { title: 'цена получена', color: 'green' },
  bounced: { title: 'отказ доставки', color: 'yellow' },
  unsubscribed: { title: 'отписался', color: 'red' },
  stopped: { title: 'цепочка остановлена', color: 'gray' },
};

export const MESSAGE_STATUSES: Record<MessageStatus, string> = {
  queued: 'в очереди',
  sending: 'отправляется',
  sent: 'принято платформой',
  delivered: 'доставлено',
  bounced: 'отказ доставки',
  stopped: 'остановлено',
};

export const REPLY_KINDS: Record<ReplyKind, { title: string; color: string }> = {
  human: { title: 'ответ человека', color: 'teal' },
  auto_reply: { title: 'автоответчик', color: 'gray' },
  bounce: { title: 'отказ доставки', color: 'yellow' },
  unsubscribe: { title: 'отписка', color: 'red' },
};
