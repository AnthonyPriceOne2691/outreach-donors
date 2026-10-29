/**
 * Файл доноров списком — проверка в браузере до отправки и слова отказа (29.10.2026).
 *
 * Пределы — те же, что у читателя файла на сервере (`donors/bulk_file.py`, сверка —
 * `tests/test_bulk_file.py`). Файл не того вида, пустой или больше предела на сервер не уходит:
 * что не так, видно у поля сразу. Больше предела его отбил бы прокси своей страницей 413, мимо
 * слов сервиса, и окно показало бы «Сервер ответил 413». Такой ответ всё равно переводится
 * в «файл больше 7 МБ» (`refusalWords`) — на случай, если предел прокси станет теснее.
 */

import { ApiError, refusalOf } from '../api/client';

/** Предел файла, МБ. */
export const FILE_LIMIT_MB = 7;
/** Строк с донорами за загрузку. */
export const ROW_LIMIT = 10_000;

/** Расширения, которые читает сервер. */
const SUFFIXES = new Set(['.csv', '.xlsx']);
/** Тип, по которому видно, что это не таблица. Список запретов, а не разрешений: свой тип
 *  таблице даёт система (CSV при Excel на Windows — `application/vnd.ms-excel`, без
 *  программы — пусто), и список разрешённых отказал бы настоящей таблице. */
const NOT_A_TABLE =
  /^(image|audio|video|font)\/|^application\/(pdf|msword|vnd\.ms-powerpoint|vnd\.openxmlformats-officedocument\.(wordprocessingml|presentationml))/;

function suffixOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot).toLowerCase();
}

export function tooBig(name: string): string {
  return `Файл «${name}» больше ${FILE_LIMIT_MB} МБ — разделите список на несколько файлов.`;
}

/** Что не так с файлом ещё до отправки; `null` — можно отправлять. Слова — как у сервера. */
export function fileRefusal(file: File): string | null {
  const suffix = suffixOf(file.name);
  if (suffix === '.xls') {
    return 'Старый формат Excel (.xls) не читается — сохраните файл как .xlsx или CSV UTF-8.';
  }
  if (!SUFFIXES.has(suffix) || NOT_A_TABLE.test(file.type)) {
    return (
      `Файл «${file.name}» — не таблица CSV или Excel (.xlsx): сохраните список как .csv ` +
      'или .xlsx, как в шаблоне.'
    );
  }
  if (file.size === 0) return `Файл «${file.name}» пуст — в нём нет ни одной строки.`;
  if (file.size > FILE_LIMIT_MB * 2 ** 20) return tooBig(file.name);
  return null;
}

/** Фраза сервера — предложением: с заглавной и с точкой. */
function sentence(text: string): string {
  const said = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?…]$/.test(said) ? said : `${said}.`;
}

/** Отказ сервера на файл `name` — словами: его текст предложением, страница 413 прокси —
 *  словами предела. */
export function refusalWords(error: unknown, name: string): string {
  if (error instanceof ApiError && error.status === 413) return tooBig(name);
  return sentence(refusalOf(error));
}
