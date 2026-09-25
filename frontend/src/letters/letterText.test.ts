/**
 * Текст письма на экране: тихие пометки туда и обратно, число отличия.
 *
 * Пометка «[имя отправителя]» — только для глаз. На сервер уходит громкая
 * метка, по которой отправка откажет: иначе письмо ушло бы подписанным
 * словами в скобках.
 */

import { describe, expect, it } from 'vitest';

import { readable, restored, uniquenessText } from './letterText';

const CORRIDOR = { min: 0.15, max: 0.25 };

const SIGNED = 'Best regards,\n«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО»\n\n«ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН»';

describe('пометки незаданного', () => {
  it('громкие метки — тихой пометкой', () => {
    const shown = readable(SIGNED);

    expect(shown.text).toBe('Best regards,\n[имя отправителя]\n\n[физический адрес]');
    expect(shown.unset).toBe(true);
    expect(readable('Hello').unset).toBe(false);
    expect(readable(null).text).toBe('');
  });

  it('правка возвращает громкие метки на место', () => {
    const edited = `${readable(SIGNED).text}\nP.S. Thank you!`;

    expect(restored(edited, SIGNED)).toBe(`${SIGNED}\nP.S. Thank you!`);
  });

  it('пометку, заменённую настоящим значением, возвращать нечем', () => {
    const edited = readable(SIGNED).text.replace('[имя отправителя]', 'Anna');

    expect(restored(edited, SIGNED)).toBe('Best regards,\nAnna\n\n«ФИЗИЧЕСКИЙ АДРЕС НЕ ЗАДАН»');
  });

  it('пометка, которой не было в исходном, остаётся как есть', () => {
    expect(restored('[имя отправителя]', 'Best regards, Anna')).toBe('[имя отправителя]');
  });
});

describe('отличие от шаблона', () => {
  it('с десятой: 0,254 — не «25%» при коридоре до 25%', () => {
    expect(uniquenessText(0.254, CORRIDOR)).toBe('25,4%');
    expect(uniquenessText(0.19, CORRIDOR)).toBe('19%');
    expect(uniquenessText(0.04, CORRIDOR)).toBe('4%');
  });

  it('у самого края коридора — точнее десятой, чтобы число не спорило с вердиктом', () => {
    expect(uniquenessText(0.2504, CORRIDOR)).toBe('25,04%');
    expect(uniquenessText(0.1496, CORRIDOR)).toBe('14,96%');
    // На самом краю — в коридоре, и лишних знаков не нужно.
    expect(uniquenessText(0.25, CORRIDOR)).toBe('25%');
    expect(uniquenessText(0.15, CORRIDOR)).toBe('15%');
  });

  it('нет числа — прочерк', () => {
    expect(uniquenessText(null, CORRIDOR)).toBe('—');
  });
});
