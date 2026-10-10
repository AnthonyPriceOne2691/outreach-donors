/**
 * Можно ли приложить файл к ответу — до загрузки, словами (`Composer`).
 *
 * **Правила файла называет сервер** (`file_rules`): тип, размер файла и письма,
 * число файлов. Здесь они проверяются до загрузки — иначе файл больше потолка
 * тела запроса отбивал бы прокси страницей без слов, — а сервер проверяет
 * всё ещё раз, по содержимому, и отказывает словами.
 *
 * **Загруженный файл ждёт в переписке**, пока ответ не уйдёт: после
 * перезагрузки страницы скрепка восстанавливает его (`pending_files`).
 */

import type { FileRules, OutgoingFile } from '../api/files';

/** Мегабайты — десятичные, как считает сервер. */
function megabytes(bytes: number): string {
  return `${Math.round((bytes / 1_000_000) * 10) / 10} МБ`;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase();
}

/** Тот же файл уже приложен — то же имя и тот же размер. */
function attachedAlready(file: File, attached: OutgoingFile[]): boolean {
  return attached.some((one) => one.name === file.name && one.size === file.size);
}

/** Почему файл не приложить — словами, до загрузки; `null` — можно. */
export function refusalBefore(
  file: File,
  attached: OutgoingFile[],
  rules: FileRules,
): string | null {
  // Второй раз тот же файл — без слов уходил на сервер и ложился рядом с первым:
  // письмо понесло бы два одинаковых вложения (проверка QA 10.10.2026).
  if (attachedAlready(file, attached)) {
    return `«${file.name}» уже приложен к ответу — второй такой же не нужен`;
  }
  if (!rules.extensions.includes(extensionOf(file.name))) {
    const allowed = rules.extensions.map((ext) => ext.toUpperCase()).join(', ');
    return `«${file.name}»: такие файлы с письмом не уходят — можно ${allowed}`;
  }
  if (file.size > rules.max_file_bytes) {
    return `«${file.name}» больше предела ${megabytes(rules.max_file_bytes)} на файл — уменьшите его или дайте ссылку в тексте ответа`;
  }
  if (attached.length >= rules.max_files) {
    return `К письму — не больше ${rules.max_files} файлов: уберите лишние`;
  }
  const total = attached.reduce((sum, one) => sum + one.size, file.size);
  if (total > rules.max_letter_bytes) {
    return `Файлы письма вместе больше предела ${megabytes(rules.max_letter_bytes)} на письмо — уберите лишние или дайте ссылку в тексте ответа`;
  }
  return null;
}
