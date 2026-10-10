/**
 * Страна словами (проверка прода 10.10.2026): на «Донорах» стояло «NP · 84%» — у кода вне
 * таблицы рынков не было имени. Рынки — нашими словами, остальные страны — именем из данных
 * браузера (`Intl.DisplayNames`), и только не страну — кодом.
 */

import { describe, expect, it } from 'vitest';

import { countryTitle } from './labels';

describe('страна словами', () => {
  it.each([
    ['np', 'Непал'],
    ['NP', 'Непал'],
    ['bd', 'Бангладеш'],
  ])('страна вне таблицы рынков — по имени: %s', (code, name) => {
    expect(countryTitle(code)).toBe(name);
  });

  it('рынок — нашим словом, а не длинным именем браузера', () => {
    expect(countryTitle('us')).toBe('США');
    expect(countryTitle('ae')).toBe('ОАЭ');
  });

  it.each(['xx', 'usa', '1'])('не страна — кодом заглавными, без падения: %s', (code) => {
    expect(countryTitle(code)).toBe(code.toUpperCase());
  });

  it('страны нет — прочерк', () => {
    expect(countryTitle(null)).toBe('—');
    expect(countryTitle('')).toBe('—');
  });
});
