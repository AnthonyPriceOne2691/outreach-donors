/**
 * Смещение пояса от UTC словами (`zoneOffset.ts`): на виду вместо имени из базы поясов.
 *
 * Проверяется то, ради чего оно считается браузером, а не своей таблицей: смещение — на момент
 * показа (зимой и летом у одного пояса разное), половины и четверти часа, запад и восток, стык
 * суток; пояс, которого браузер не знает, — `null`, а не выдуманное смещение. Часы теста
 * закреплены: без момента смещение берётся «сейчас», и назавтра тест проверял бы другое.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { offsetLabel, offsetWords, wallTime, zoneOffset } from './zoneOffset';

const WINTER = new Date('2026-01-15T10:00:00Z');
const SUMMER = new Date('2026-07-15T10:00:00Z');

afterEach(() => {
  vi.useRealTimers();
});

describe('смещение пояса от UTC', () => {
  it('целые часы к востоку и к западу; у UTC — ноль', () => {
    expect(zoneOffset('Asia/Tashkent', WINTER)).toBe('UTC+5');
    expect(zoneOffset('Asia/Tokyo', WINTER)).toBe('UTC+9');
    expect(zoneOffset('America/Argentina/Buenos_Aires', WINTER)).toBe('UTC−3');
    expect(zoneOffset('UTC', WINTER)).toBe('UTC+0');
  });

  it('летнее время — на момент показа: у одного пояса зимой и летом разное', () => {
    expect(zoneOffset('Europe/Berlin', WINTER)).toBe('UTC+1');
    expect(zoneOffset('Europe/Berlin', SUMMER)).toBe('UTC+2');
    expect(zoneOffset('America/Toronto', WINTER)).toBe('UTC−5');
    expect(zoneOffset('America/Toronto', SUMMER)).toBe('UTC−4');
    expect(zoneOffset('Europe/London', WINTER)).toBe('UTC+0');
    expect(zoneOffset('Europe/London', SUMMER)).toBe('UTC+1');
  });

  it('половины и четверти часа — с минутами', () => {
    expect(zoneOffset('Asia/Kolkata', WINTER)).toBe('UTC+5:30');
    expect(zoneOffset('Asia/Kathmandu', WINTER)).toBe('UTC+5:45');
    expect(zoneOffset('America/St_Johns', WINTER)).toBe('UTC−3:30');
  });

  it('стык суток и секунды момента смещения не сдвигают', () => {
    // 23:59:59 UTC: к востоку уже завтра, к западу — ещё сегодня; год и месяц — тоже на стыке.
    const edge = new Date('2026-12-31T23:59:59.999Z');
    expect(zoneOffset('Pacific/Kiritimati', edge)).toBe('UTC+14');
    expect(zoneOffset('Pacific/Pago_Pago', edge)).toBe('UTC−11');
    expect(zoneOffset('Asia/Kolkata', edge)).toBe('UTC+5:30');
    // Полночь на часах пояса — смещение то же.
    expect(zoneOffset('Asia/Tashkent', new Date('2026-01-15T19:00:00Z'))).toBe('UTC+5');
    expect(zoneOffset('Asia/Tashkent', new Date('2026-01-15T19:30:00Z'))).toBe('UTC+5');
  });

  it('полночь, записанная часом «24» с датой нового дня, — начало этих суток, а не следующих', () => {
    // Так пишет полночь часть браузеров: «1/16/2026, 24:30» — это 00:30 шестнадцатого.
    const clock = (hour: string): Intl.DateTimeFormatPart[] => [
      { type: 'month', value: '1' },
      { type: 'literal', value: '/' },
      { type: 'day', value: '16' },
      { type: 'literal', value: '/' },
      { type: 'year', value: '2026' },
      { type: 'literal', value: ', ' },
      { type: 'hour', value: hour },
      { type: 'literal', value: ':' },
      { type: 'minute', value: '30' },
    ];

    expect(wallTime(clock('24'))).toBe(Date.UTC(2026, 0, 16, 0, 30));
    expect(wallTime(clock('0'))).toBe(Date.UTC(2026, 0, 16, 0, 30));
    expect(wallTime(clock('23'))).toBe(Date.UTC(2026, 0, 16, 23, 30));
  });

  it('без момента — сейчас, по часам браузера', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(SUMMER);
    expect(zoneOffset('Europe/Berlin')).toBe('UTC+2');
    vi.setSystemTime(WINTER);
    expect(zoneOffset('Europe/Berlin')).toBe('UTC+1');
  });

  it('пояса нет в базе поясов браузера или момент — не дата: смещения нет', () => {
    expect(zoneOffset('Mars/Olympus', WINTER)).toBeNull();
    expect(zoneOffset('', WINTER)).toBeNull();
    // Второй раз — тот же ответ: имя, которого браузер не знает, запомнено, а не спрошено заново.
    expect(zoneOffset('Mars/Olympus', SUMMER)).toBeNull();
    expect(zoneOffset('Europe/Berlin', new Date('не дата'))).toBeNull();
  });

  it('минуты словами: знак минуса, а не дефис; ноль — «UTC+0»', () => {
    expect(offsetLabel(300)).toBe('UTC+5');
    expect(offsetLabel(-210)).toBe('UTC−3:30');
    expect(offsetLabel(345)).toBe('UTC+5:45');
    expect(offsetLabel(0)).toBe('UTC+0');
    expect(offsetLabel(-5)).toBe('UTC−0:05');
  });
});

describe('слова сервера: имя пояса → смещение', () => {
  const said = 'окно получателя на сегодня закрылось в 12:00 UTC (пояс Asia/Tashkent)';

  it('имя, которое назвал сервер, — смещением; остальные слова — как есть', () => {
    expect(offsetWords(said, 'Asia/Tashkent', WINTER)).toBe(
      'окно получателя на сегодня закрылось в 12:00 UTC (пояс UTC+5)',
    );
    expect(
      offsetWords('по часам Europe/Berlin и ещё раз Europe/Berlin', 'Europe/Berlin', SUMMER),
    ).toBe('по часам UTC+2 и ещё раз UTC+2');
  });

  it('пояса нет, он пуст, не тот или браузер его не знает — слова как есть', () => {
    expect(offsetWords(said, null, WINTER)).toBe(said);
    expect(offsetWords(said, '', WINTER)).toBe(said);
    expect(offsetWords(said, 'Europe/Berlin', WINTER)).toBe(said);
    expect(offsetWords('по часам Mars/Olympus', 'Mars/Olympus', WINTER)).toBe(
      'по часам Mars/Olympus',
    );
  });
});
