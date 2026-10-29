/**
 * Файл доноров списком до отправки (`bulkFile.ts`): что уходит на сервер, что нет — и слова
 * отказа. Проверка не отказывает настоящей таблице (тип CSV у Excel на Windows — тип Excel,
 * регистр расширения — любой), а предел — ровно тот, что у сервера: больше 7 МБ — нет.
 */

import { describe, expect, it } from 'vitest';

import { ApiError, NO_CONNECTION, NoConnectionError } from '../api/client';
import { FILE_LIMIT_MB, fileRefusal, refusalWords, tooBig } from './bulkFile';

function sized(name: string, type: string, size: number): File {
  const file = new File(['домен,цена\n'], name, { type });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

describe('файл до отправки', () => {
  it.each([
    ['доноры.csv', 'text/csv'],
    ['ДОНОРЫ.CSV', ''],
    // Excel на Windows даёт CSV свой тип — это всё ещё таблица.
    ['доноры.csv', 'application/vnd.ms-excel'],
    ['доноры.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    ['доноры.xlsx', 'application/octet-stream'],
  ])('%s (%s) уходит', (name, type) => {
    expect(fileRefusal(sized(name, type, 100))).toBeNull();
  });

  it.each([['доноры'], ['доноры.txt'], ['доноры.docx'], ['доноры.csv.pdf']])(
    '%s — не таблица',
    (name) => {
      expect(fileRefusal(sized(name, '', 100))).toBe(
        `Файл «${name}» — не таблица CSV или Excel (.xlsx): сохраните список как .csv или .xlsx, как в шаблоне.`,
      );
    },
  );

  it('предел — ровно 7 МБ: больше — нет', () => {
    expect(FILE_LIMIT_MB).toBe(7);
    expect(fileRefusal(sized('x.csv', 'text/csv', 7 * 2 ** 20))).toBeNull();
    expect(fileRefusal(sized('x.csv', 'text/csv', 7 * 2 ** 20 + 1))).toBe(tooBig('x.csv'));
  });
});

describe('слова отказа сервера', () => {
  it('страница 413 прокси — словами предела', () => {
    expect(refusalWords(new ApiError(413, 'Сервер ответил 413'), 'x.csv')).toBe(
      'Файл «x.csv» больше 7 МБ — разделите список на несколько файлов.',
    );
  });

  it('фраза сервера — предложением, точка не удваивается', () => {
    expect(
      refusalWords(new ApiError(400, 'файл «x.csv» пуст — в нём нет ни одной строки'), 'x.csv'),
    ).toBe('Файл «x.csv» пуст — в нём нет ни одной строки.');
    expect(refusalWords(new ApiError(400, 'Сейчас в первой строке: имя, сумма…'), 'x')).toBe(
      'Сейчас в первой строке: имя, сумма…',
    );
  });

  it('обрыв связи — словами клиента; не ошибка — честное «без объяснения»', () => {
    expect(refusalWords(new NoConnectionError(new TypeError('Failed to fetch')), 'x')).toBe(
      `${NO_CONNECTION}.`,
    );
    expect(refusalWords('нечто', 'x')).toBe('Сервер отказал без объяснения.');
  });
});
