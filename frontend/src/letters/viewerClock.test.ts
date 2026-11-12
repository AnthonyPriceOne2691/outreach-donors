/**
 * Время из слов сервера — по часам смотрящего (`viewerClock.ts`): слова прохода автоотправки
 * называют время по UTC, а момент прохода перед ними экран пишет по часам браузера.
 *
 * Пояс в тестах назван, как в `sales/zoneOffset.test.ts`, а не взят у машины: на UTC (CI) перевод
 * по часам машины ничего бы не менял, и тест был бы зелёным при любой ошибке в нём. Часы машины —
 * отдельным случаем, моментами, собранными местным временем (как `deliveryLine.test.ts`): он
 * читается одинаково в любом поясе.
 */

import { describe, expect, it } from 'vitest';

import { formatDateTime, formatTime } from '../format';
import { viewerClockWords } from './viewerClock';

/** Слова прохода (`letters/autosend.py`, `_nobody`) и его момент; числа и часы выдуманы. */
const RESTING = 'ушло 2. ящики на паузе между письмами — ближайший освободится в';
const PASSED = '2026-11-12T11:17:10Z';

describe('время прохода — по часам смотрящего', () => {
  it('«ЧЧ:ММ UTC» — тем же временем по своим часам и без «UTC»', () => {
    const said = `${RESTING} 11:18 UTC`;

    expect(viewerClockWords(said, PASSED, 'Europe/Moscow')).toBe(`${RESTING} 14:18`);
    expect(viewerClockWords(said, PASSED, 'Asia/Tashkent')).toBe(`${RESTING} 16:18`);
    expect(viewerClockWords(said, PASSED, 'America/Toronto')).toBe(`${RESTING} 06:18`);
    // Смотрящий на UTC: время то же, а пометки нет — вся строка по его часам.
    expect(viewerClockWords(said, PASSED, 'UTC')).toBe(`${RESTING} 11:18`);
  });

  it('половины часа пояса — с минутами', () => {
    const said = `${RESTING} 11:18 UTC`;

    expect(viewerClockWords(said, PASSED, 'Asia/Kolkata')).toBe(`${RESTING} 16:48`);
    expect(viewerClockWords(said, PASSED, 'America/St_Johns')).toBe(`${RESTING} 07:48`);
  });

  // В Берлине летнее время кончается 25.10.2026 в 01:00 UTC: до него пояс UTC+2, после — UTC+1.
  // На этом стыке видно, какой день взят для часов без даты.
  it('день — от момента прохода: время не раньше него — тот же день', () => {
    const passed = '2026-10-24T23:58:40Z';

    // Та же минута, что у прохода: секунд в словах нет, и это не «завтра в 23:58».
    expect(viewerClockWords(`${RESTING} 23:58 UTC`, passed, 'Europe/Berlin')).toBe(
      `${RESTING} 01:58`,
    );
  });

  it('время раньше прохода — следующий день: ящик освобождается после прохода', () => {
    const passed = '2026-10-24T23:58:40Z';

    // 01:30 UTC следующего дня — уже зимнее время: 02:30; днём прохода вышло бы 03:30.
    expect(viewerClockWords(`${RESTING} 01:30 UTC`, passed, 'Europe/Berlin')).toBe(
      `${RESTING} 02:30`,
    );
  });

  it('каждое время в словах — своим: меняется не только первое', () => {
    expect(
      viewerClockWords('до 15:00 UTC успеет 4; освободится в 11:18 UTC.', PASSED, 'Europe/Moscow'),
    ).toBe('до 18:00 успеет 4; освободится в 14:18.');
  });

  it('без названного пояса — по часам браузера, тем же способом, что момент прохода', () => {
    // Моменты — местным временем: строка одна и та же в Москве и на UTC.
    const passed = new Date(2026, 10, 12, 14, 17, 10);
    const free = new Date(2026, 10, 12, 14, 18);
    const two = (part: number) => String(part).padStart(2, '0');
    const said = `${RESTING} ${two(free.getUTCHours())}:${two(free.getUTCMinutes())} UTC`;

    const shown = viewerClockWords(said, passed.toISOString());

    expect(shown).toBe(`${RESTING} 14:18`);
    expect(shown).toBe(`${RESTING} ${formatTime(free.toISOString())}`);
    // Строка экрана целиком: момент прохода и время в словах — в одних часах.
    expect(`${formatDateTime(passed.toISOString())}: ${shown}`).toBe(
      `12.11.2026, 14:17: ${RESTING} 14:18`,
    );
  });
});

describe('время с датой — вместе с датой', () => {
  const YOUNG = 'домен на выдержке: mail-a.example.test — до';

  it('дата — тоже по своим часам: на востоке уже следующий день', () => {
    const said = `${YOUNG} 13.11 22:30 UTC`;

    expect(viewerClockWords(said, PASSED, 'Asia/Tashkent')).toBe(`${YOUNG} 14.11 03:30`);
    expect(viewerClockWords(said, PASSED, 'America/Toronto')).toBe(`${YOUNG} 13.11 17:30`);
  });

  it('дата с годом и запятой — теми же частями', () => {
    expect(viewerClockWords('решено 12.11.2026, 22:30 UTC', PASSED, 'Asia/Tashkent')).toBe(
      'решено 13.11.2026, 03:30',
    );
    expect(viewerClockWords('решено 12.11.2026 22:30 UTC', PASSED, 'America/Toronto')).toBe(
      'решено 12.11.2026 17:30',
    );
  });

  it('год у даты без года — ближайший к моменту: 31 декабря «до 01.01» — это завтра', () => {
    // Москва 01.01.2014 — UTC+4, 01.01.2015 — UTC+3: по смещению видно, какой год взят.
    expect(
      viewerClockWords(`${YOUNG} 01.01 03:00 UTC`, '2014-12-31T20:00:00Z', 'Europe/Moscow'),
    ).toBe(`${YOUNG} 01.01 06:00`);
  });

  it('29 февраля — в том году, где оно есть; такого дня рядом нет — слова как есть', () => {
    expect(viewerClockWords(`${YOUNG} 29.02 01:00 UTC`, '2027-12-30T10:00:00Z', 'UTC')).toBe(
      `${YOUNG} 29.02 01:00`,
    );
    const never = `${YOUNG} 29.02 01:00 UTC`;
    expect(viewerClockWords(never, '2026-06-15T10:00:00Z', 'UTC')).toBe(never);
    const wrong = `${YOUNG} 31.02 10:00 UTC`;
    expect(viewerClockWords(wrong, PASSED, 'UTC')).toBe(wrong);
  });
});

describe('слова как есть', () => {
  it('времени по UTC в словах нет', () => {
    const empty = 'очередь кончилась — новые письма уйдут, когда очередь снова отправят';
    // Время получателя, а не UTC (`letters/autosend_window.py`): его экран не переводит.
    const window =
      'ждёт рабочего времени получателей — ближайшее откроется в 09:00 по их времени (пн 16.11, Europe/Berlin)';

    expect(viewerClockWords(empty, PASSED, 'Europe/Moscow')).toBe(empty);
    expect(viewerClockWords(window, PASSED, 'Europe/Moscow')).toBe(window);
    expect(viewerClockWords('', PASSED, 'Europe/Moscow')).toBe('');
  });

  it('момента прохода нет или он не разбирается', () => {
    const said = `${RESTING} 11:18 UTC`;

    expect(viewerClockWords(said, null, 'Europe/Moscow')).toBe(said);
    expect(viewerClockWords(said, undefined, 'Europe/Moscow')).toBe(said);
    expect(viewerClockWords(said, '', 'Europe/Moscow')).toBe(said);
    expect(viewerClockWords(said, 'не дата', 'Europe/Moscow')).toBe(said);
  });

  it('не время: часов и минут таких нет, хвост числа, время с секундами', () => {
    for (const said of [
      'освободится в 25:00 UTC',
      'освободится в 10:75 UTC',
      'номер 111:18 UTC',
      'в 12:30:45 UTC',
    ]) {
      expect(viewerClockWords(said, PASSED, 'Europe/Moscow')).toBe(said);
    }
  });

  it('«UTC+5» — смещение пояса, а не время по UTC', () => {
    for (const said of [
      'закрылось в 12:00 UTC+5',
      'закрылось в 12:00 UTC−5',
      'закрылось в 12:00 UTC-5',
    ]) {
      expect(viewerClockWords(said, PASSED, 'Europe/Moscow')).toBe(said);
    }
    // Время по UTC и смещение пояса рядом (`outreach/capacity.py` после `offsetWords`): время —
    // по своим часам, смещение — как есть.
    expect(viewerClockWords('закрылось в 12:00 UTC (пояс UTC−5)', PASSED, 'Europe/Moscow')).toBe(
      'закрылось в 15:00 (пояс UTC−5)',
    );
  });

  it('дата в незнакомом виде — время рядом с ней не трогается', () => {
    for (const said of ['до 2026-11-13 22:30 UTC', 'до 3.11 22:30 UTC', 'до 13/11 22:30 UTC']) {
      expect(viewerClockWords(said, PASSED, 'Asia/Tashkent')).toBe(said);
    }
  });
});
