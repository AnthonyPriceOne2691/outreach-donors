/**
 * Человек за экраном: что считается его действием (аудит 20.11.2026, простой сеанса).
 *
 * Запрос без человека уходит фоновым и сеанс на сервере не продлевает (`api/client.ts`).
 * Прокрутка действием не считается: её делает и сам экран.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { AWAY_AFTER_MS, noteInput, personAway, watchPresence } from './presence';

/** Давно, дольше минуты: человек ушёл. */
const LONG_AGO = () => Date.now() - 2 * AWAY_AFTER_MS;

/** Слушатели теста снимаются и тогда, когда проверка упала раньше: иначе они слушали бы
 *  действия следующего теста. */
const stops: (() => void)[] = [];

function watched(): void {
  stops.push(watchPresence());
}

describe('человек за экраном', () => {
  afterEach(() => {
    for (const stop of stops.splice(0)) stop();
    noteInput();
  });

  it('минута без действий — человека нет, меньше минуты — есть', () => {
    const now = Date.now();

    noteInput(now - AWAY_AFTER_MS);
    expect(personAway(now)).toBe(true);
    noteInput(now - AWAY_AFTER_MS + 1000);
    expect(personAway(now)).toBe(false);
  });

  it.each(['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'click', 'input'])(
    '«%s» — действие человека',
    (type) => {
      watched();
      noteInput(LONG_AGO());

      window.dispatchEvent(new Event(type));

      expect(personAway()).toBe(false);
    },
  );

  it('прокрутка — не действие: её делает и сам экран', () => {
    watched();
    noteInput(LONG_AGO());

    window.dispatchEvent(new Event('scroll'));

    expect(personAway()).toBe(true);
  });

  it('после «перестать» действия больше не слушаются', () => {
    watchPresence()();
    noteInput(LONG_AGO());

    window.dispatchEvent(new Event('keydown'));

    expect(personAway()).toBe(true);
  });
});
