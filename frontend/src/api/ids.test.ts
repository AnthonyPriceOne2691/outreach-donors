/**
 * Номер записи из адреса: что считается номером, а что — нет.
 *
 * `Number()` понимает слишком много: «1e3», «12.5» и « 7 » для него числа,
 * а записи с таким номером не бывает — и запрос ушёл бы на сервер, получив
 * английский отказ разбора. Номер — только цифры, с единицы, в пределах
 * столбца базы.
 */

import { describe, expect, it } from 'vitest';

import { MAX_ROW_ID, rowIdOf } from './ids';

describe('номер записи из адреса', () => {
  it.each(['1', '42', String(MAX_ROW_ID)])('«%s» — номер', (raw) => {
    expect(rowIdOf(raw)).toBe(Number(raw));
  });

  it.each([
    undefined,
    '',
    'abc',
    'NaN',
    '0',
    '-1',
    '12.5',
    '1e3',
    ' 7',
    '0x10',
    String(MAX_ROW_ID + 1),
    '99999999999',
  ])('«%s» — не номер', (raw) => {
    expect(rowIdOf(raw)).toBeNull();
  });
});
