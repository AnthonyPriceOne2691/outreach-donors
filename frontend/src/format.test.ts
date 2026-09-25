/**
 * Общие функции чисел: деньги, процент с десятой, слово при числе.
 *
 * Деньги на экранах диалогов шли сырой строкой сервера — «250.00 / 180.00
 * EUR», с точкой и без разрядов, — рядом с расходом «0,72 $». Процент
 * отличия письма шёл целыми — «25% выше коридора 15–25%» при 0,254.
 */

import { describe, expect, it } from 'vitest';

import { formatMoney, formatPercent, formatUsd, plural } from './format';

/** Разряды у `Intl` — неразрывным пробелом; сравниваем по обычному. */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

describe('деньги', () => {
  it('разряды, запятая и знак валюты', () => {
    expect(plain(formatMoney('1250.00', 'EUR'))).toBe('1 250,00 €');
    expect(plain(formatMoney(320, 'USD'))).toBe('320,00 $');
    expect(plain(formatMoney('9.5', 'gbp'))).toBe('9,50 £');
  });

  it('валюта без знака пишется кодом, не названная — не пишется', () => {
    expect(plain(formatMoney('300', 'USDT'))).toBe('300,00 USDT');
    expect(plain(formatMoney('300', null))).toBe('300,00');
    expect(plain(formatMoney('300', '  '))).toBe('300,00');
  });

  it('пусто и не число — прочерк, а не «0,00»', () => {
    expect(formatMoney(null, 'EUR')).toBe('—');
    expect(formatMoney(undefined, 'EUR')).toBe('—');
    expect(formatMoney('', 'EUR')).toBe('—');
    expect(formatMoney('не число', 'EUR')).toBe('—');
  });

  it('доллары расхода — той же функцией', () => {
    expect(plain(formatUsd('0.7194'))).toBe('0,72 $');
    expect(plain(formatUsd(0))).toBe('0,00 $');
  });
});

describe('процент с десятой', () => {
  it('десятая видна, нулевой хвост — нет', () => {
    expect(formatPercent(0.254)).toBe('25,4%');
    expect(formatPercent(0.19)).toBe('19%');
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(1)).toBe('100%');
  });

  it('точнее, когда просят', () => {
    expect(formatPercent(0.2504, 2)).toBe('25,04%');
    expect(formatPercent(0.2504, 1)).toBe('25%');
  });

  it('пусто — прочерк', () => {
    expect(formatPercent(null)).toBe('—');
    expect(formatPercent(undefined)).toBe('—');
    expect(formatPercent(Number.NaN)).toBe('—');
  });
});

describe('слово при числе', () => {
  it('один, два, пять — и одиннадцать', () => {
    const letters = (count: number) => plural(count, 'письмо', 'письма', 'писем');
    expect(letters(1)).toBe('письмо');
    expect(letters(4)).toBe('письма');
    expect(letters(5)).toBe('писем');
    expect(letters(11)).toBe('писем');
    expect(letters(21)).toBe('письмо');
    expect(letters(112)).toBe('писем');
    expect(letters(2_180_924)).toBe('письма');
  });
});
