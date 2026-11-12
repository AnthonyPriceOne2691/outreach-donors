/**
 * Ночной обход строкой на вкладке (02.11.2026, `crawl/night.py`).
 *
 * Проверяется то, ради чего строка: круг идёт ночью, и утром по ней видно, когда он был, сколько
 * доноров, что считали и что осталось; выключен, пропущен и пуст — словами, а не тишиной.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { CrawlNightRow, CrawlNightView } from '../api/crawls';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import {
  CATALOGS_WHY,
  CrawlNight,
  nightCatalogs,
  nightClock,
  nightDay,
  nightFacts,
  nightWords,
} from './CrawlNight';

const ROW: CrawlNightRow = {
  night: '2026-11-03',
  status: 'done',
  started_at: '2026-11-02T21:00:00Z',
  issue_until: '2026-11-03T02:30:00Z',
  finished_at: '2026-11-03T03:40:00Z',
  note: null,
  taken: 120,
  given: 118,
  dropped: 2,
  going: 0,
  waiting: 0,
  workers: 16,
  slots: 12,
  typical_sec: 1740,
  longest_sec: 3600,
  estimate_sec: 21_000,
  donors: 340,
  left_out: 220,
  rested: 0,
  catalogs: 0,
  ranks: { limit: 80, no_start: 30, partial: 10 },
};

function view(last: Partial<CrawlNightRow> | null, enabled = true): CrawlNightView {
  return { enabled, window_sec: 25_200, last: last === null ? null : { ...ROW, ...last } };
}

/** Текст без неразрывных пробелов разрядов. */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

describe('слова строки', () => {
  it('день и часы — по Москве', () => {
    expect(nightDay('2026-11-03')).toBe('03.11');
    expect(nightClock('2026-11-03T02:30:00Z')).toBe('05:30');
  });

  it('выключен — так и сказано, а не пусто', () => {
    expect(nightWords(view(null, false))).toBe('Ночной обход выключен: доноры обходятся кнопками.');
    expect(nightWords(view({}, false))).toBe('Ночной обход выключен: доноры обходятся кнопками.');
  });

  it('выключили посреди круга — круг ещё идёт, и это видно', () => {
    expect(nightWords(view({ status: 'running', given: 3, taken: 9 }, false))).toContain(
      'отдано обходчикам 3 из 9',
    );
  });

  it('кругов ещё не было — когда начнётся и за сколько', () => {
    expect(nightWords(view(null))).toBe(
      'Ночной обход начнётся в полночь по Москве и уложится в 7 ч.',
    );
  });

  it('идёт — сколько отдано, сколько ждёт и до какого часа выдача', () => {
    expect(plain(nightWords(view({ status: 'running', given: 40, taken: 120, waiting: 80 })))).toBe(
      'Ночной обход идёт: отдано обходчикам 40 из 120, ждут 80, новые — до 05:30.',
    );
  });

  it('кончился — сколько пошло, что снято и кто ждёт следующей ночи', () => {
    expect(plain(nightWords(view({})))).toBe(
      'Ночной обход 03.11: пошло 118 из 120 доноров, снято по сроку 2; ' +
        'не поместились 220 доноров — следующими ночами.',
    );
  });

  it('все поместились и никого не сняли — без хвоста', () => {
    expect(plain(nightWords(view({ dropped: 0, left_out: 0, given: 120 })))).toBe(
      'Ночной обход 03.11: пошло 120 из 120 доноров.',
    );
  });

  it('обходить было некого и мощности не хватило — разные новости', () => {
    expect(nightWords(view({ taken: 0, donors: 0 }))).toBe(
      'Ночной обход 03.11: обходить было некого.',
    );
    expect(nightWords(view({ taken: 0, donors: 5 }))).toContain('мощности не хватило');
  });

  it('пропущен и упал — словами сервера', () => {
    expect(
      nightWords(view({ status: 'missed', note: 'процесс не работал до 02:00', workers: null })),
    ).toBe('Ночной обход 03.11 пропущен: процесс не работал до 02:00.');
    expect(nightWords(view({ status: 'failed', note: 'расчёт не удался' }))).toBe(
      'Ночной обход 03.11 не начался: расчёт не удался.',
    );
  });
});

describe('расчёт в «i»', () => {
  it('из чего вышло: обходчики, длина обхода, срок и очереди', () => {
    const lines = nightFacts(ROW)?.map(plain) ?? [];
    expect(lines[0]).toBe('Обходчиков 16, свободно под круг 12.');
    expect(lines[1]).toBe('Обход донора обычно 29 мин, долгий — час.');
    expect(lines[2]).toBe('Круг рассчитан на 5 ч 50 мин.');
    expect(lines[3]).toBe(
      'Дошли до потолка: 80; не начались или ещё не обходили: 30; обойдены не до потолка: 10.',
    );
  });

  it('отложенные после глубокого обхода — отдельной строкой, если есть', () => {
    expect(nightFacts({ ...ROW, rested: 3 })?.at(-1)).toContain('Обойдены глубоко недавно');
    expect(nightFacts(ROW)?.some((line) => line.includes('глубоко'))).toBe(false);
  });

  it('расчёта не было — «i» нет', () => {
    expect(nightFacts(null)).toBeNull();
    expect(nightFacts({ ...ROW, workers: null })).toBeNull();
  });
});

describe('доноры-каталоги, которых круг отложил (12.11.2026)', () => {
  it('число — у круга, о котором говорит строка; ноля и числа до 11.11 нет', () => {
    expect(nightCatalogs(view({ catalogs: 3 }))).toBe(3);
    expect(nightCatalogs(view({ status: 'running', catalogs: 3 }))).toBe(3);
    expect(nightCatalogs(view({}))).toBe(0);
    expect(nightCatalogs(view({ catalogs: null }))).toBe(0);
    expect(nightCatalogs(view(null))).toBe(0);
  });

  it('круг выключен — строка не о нём, и число молчит; идущий круг — ещё о нём', () => {
    expect(nightCatalogs(view({ catalogs: 3 }, false))).toBe(0);
    expect(nightCatalogs(view({ status: 'running', catalogs: 3 }, false))).toBe(3);
  });

  it('почему — подсказкой: до 75 знаков', () => {
    expect(CATALOGS_WHY.length).toBeLessThanOrEqual(75);
  });
});

describe('строка на экране', () => {
  it('рисует слова сервера и открывает расчёт', async () => {
    serve({ 'GET /api/crawls/night': { body: view({}) } });
    renderWith(<CrawlNight />, '/');

    expect(await screen.findByText(/пошло 118 из 120/)).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'Из чего вышел расчёт ночного обхода' }));
    expect(await screen.findByText(/свободно под круг 12/)).toBeInTheDocument();
  });

  it('каталоги отложены — число рядом со строкой, почему — в его «i»', async () => {
    serve({ 'GET /api/crawls/night': { body: view({ catalogs: 1234 }) } });
    renderWith(<CrawlNight />, '/');

    // Число не отрывается от подписи: строкой текста искать нельзя — поиск схлопнул бы пробел.
    const said = await screen.findByText(/каталогов отложено:/);
    expect(said.textContent).toBe('· каталогов отложено:\u00a01\u00a0234');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Почему каталоги отложены' }));
    expect(await screen.findByText(CATALOGS_WHY)).toBeInTheDocument();
  });

  it('каталогов не откладывали — о них ни слова, «i» один', async () => {
    serve({ 'GET /api/crawls/night': { body: view({}) } });
    renderWith(<CrawlNight />, '/');

    expect(await screen.findByText(/пошло 118 из 120/)).toBeInTheDocument();
    expect(screen.queryByText(/каталогов отложено/)).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  it('выключен — без «i»', async () => {
    serve({ 'GET /api/crawls/night': { body: view(null, false) } });
    renderWith(<CrawlNight />, '/');

    expect(await screen.findByText(/выключен/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
