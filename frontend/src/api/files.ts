/**
 * Файлы переписки: вложения ответа собеседника — с текстом, прочитанным для разбора
 * цены, и файлы наших ответов — скрепкой.
 *
 * Своим файлом, а не в `types.ts`: тот у потолка длины. Поля, которые сервер
 * добавил к прежним карточкам 09.10.2026, — расширениями прежних типов,
 * необязательными: экран, открытый до выкатки, их не получит.
 */

import type { LetterCard, ReplyAttachment } from './types';

/** Вложение ответа с тем, что из него прочитано (`replies/attachment_text`). */
export interface ReplyFile extends ReplyAttachment {
  /** Из файла прочитан текст — его можно открыть на экране, не скачивая файл. */
  has_text?: boolean;
  /** Почему текста нет или он неполный — словами сервера («старый формат Excel —
   *  файл можно скачать», «обрезано»). Пусто у файла, который ещё не читали. */
  text_note?: string | null;
}

/** Текст вложения, каким его видела модель разбора цены. */
export interface AttachmentText {
  name: string;
  text: string | null;
  note: string | null;
}

/** Файл нашего письма — на сервере, без тела: имя и размер для значка. */
export interface OutgoingFile {
  id: number;
  name: string;
  size: number;
  content_type?: string;
}

/** Наше письмо с приложенными к ответу файлами. */
export interface LetterWithFiles extends LetterCard {
  attachments?: OutgoingFile[];
}

/** Правила файла к ответу — называет сервер (`letters/outgoing_files`): копия
 *  чисел во фронте разошлась бы с его проверкой при первой правке. */
export interface FileRules {
  max_file_bytes: number;
  max_letter_bytes: number;
  max_files: number;
  /** Расширения в нижнем регистре, без точки. */
  extensions: string[];
  /** Сколько дней приложенный файл ждёт письма — потом убирается сам. */
  pending_days?: number;
}
