/**
 * Фильтры под каждой колонкой доноров: трафик, страна, данные.
 *
 * Замечание 26.09.2026: «добить все фильтры (сделать и для других колонок)».
 * Проверяется, что фильтр уходит на сервер параметром, а не фильтрует
 * пришедшую страницу, что он живёт в адресе и что пустой результат называет
 * его словами. И что доноров нет вовсе — это путь к рассмотрению прогона,
 * а не «база пуста».
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorsPage } from '../api/types';
import { CONTACTS_STATE, page, ROWS } from '../test/donorFixtures';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

function donorsAt(query: string): string {
  return `GET /api/donors?${query}`;
}

async function openDonors(
  routes: Record<string, Answer> = {},
  { path = '/donors', ready = 'good.example.test' }: { path?: string; ready?: string } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts': { body: CONTACTS_STATE },
    [donorsAt('limit=20&offset=0')]: { body: page() },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready);
  return recorded;
}

function listCalls(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/donors?'))
    .map((call: Call) => call.path);
}

/** Пункты списка одного поля: на строке фильтров списков несколько. */
function optionsOf(field: HTMLElement) {
  return within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
}

async function choose(fieldName: string, option: string) {
  const user = userEvent.setup();
  const field = screen.getByRole('textbox', { name: fieldName });
  await user.click(field);
  await user.click(await optionsOf(field).findByRole('option', { name: option, hidden: true }));
}

describe('доноры: фильтры трафика, страны и данных', () => {
  it('из адреса — в запрос и в поля, трафик с разрядами', async () => {
    const recorded = await openDonors(
      {
        [donorsAt('min_traffic=1000000&geo=us&freshness=fresh&limit=20&offset=0')]: {
          body: page({ rows: [ROWS[0]!], total: 1 }),
        },
      },
      { path: '/donors?min_traffic=1000000&geo=us&freshness=fresh' },
    );

    expect(listCalls(recorded)).toEqual([
      '/api/donors?min_traffic=1000000&geo=us&freshness=fresh&limit=20&offset=0',
    ]);
    expect(screen.getByRole('textbox', { name: 'Трафик не ниже' })).toHaveValue('1 000 000');
    // В поле страны — название: по нему ищут; счётчик — в списке.
    expect(screen.getByRole('textbox', { name: 'Гео' })).toHaveValue('США');
    expect(screen.getByRole('textbox', { name: 'Данные' })).toHaveValue('в сроке · 1');
    expect(screen.getByText('найдено 1 из 3')).toBeInTheDocument();
  });

  it('порог трафика уходит после паузы в наборе, а не по цифре', async () => {
    const recorded = await openDonors({
      [donorsAt('min_traffic=1000000&limit=20&offset=0')]: { body: page() },
    });
    const user = userEvent.setup();

    await user.type(screen.getByRole('textbox', { name: 'Трафик не ниже' }), '1000000');

    // Семь знаков с разрядами печатаются дольше двух у DR: ждём с запасом.
    await waitFor(
      () =>
        expect(listCalls(recorded).at(-1)).toBe(
          '/api/donors?min_traffic=1000000&limit=20&offset=0',
        ),
      { timeout: 3000 },
    );
    expect(listCalls(recorded).filter((path) => path.includes('min_traffic='))).toEqual([
      '/api/donors?min_traffic=1000000&limit=20&offset=0',
    ]);
    expect(screen.getByRole('textbox', { name: 'Трафик не ниже' })).toHaveValue('1 000 000');
  });

  it('страна — из тех, что есть у доноров, со счётчиками и поиском по названию', async () => {
    const recorded = await openDonors({
      [donorsAt('geo=de&limit=20&offset=0')]: { body: page({ rows: [ROWS[1]!], total: 1 }) },
    });
    const user = userEvent.setup();
    const field = screen.getByRole('textbox', { name: 'Гео' });

    await user.click(field);
    const options = optionsOf(field);
    expect(
      (await options.findAllByRole('option', { hidden: true })).map((o) => o.textContent),
    ).toEqual(['все · 3', 'Германия · 1', 'США · 1']);
    await user.clear(field);
    await user.type(field, 'герм');
    expect(options.getAllByRole('option', { hidden: true }).map((o) => o.textContent)).toEqual([
      'Германия · 1',
    ]);
    await user.click(options.getByRole('option', { name: 'Германия · 1', hidden: true }));

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/donors?geo=de&limit=20&offset=0'),
    );
  });

  it('данные — в сроке, пора обновить, не проверялись — со счётчиками', async () => {
    const recorded = await openDonors({
      [donorsAt('freshness=stale&limit=20&offset=0')]: {
        body: page({ rows: [ROWS[1]!], total: 1 }),
      },
    });

    await choose('Данные', 'пора обновить · 1');

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/donors?freshness=stale&limit=20&offset=0'),
    );
    // Значок в строке — словом того же правила, что фильтр.
    const row = (await screen.findByText('weak.example.test')).closest('tr')!;
    expect(within(row).getByText('пора обновить')).toBeInTheDocument();
  });

  it('пустой результат называет новые условия словами', async () => {
    const nothing: DonorsPage = page({ rows: [], total: 0 });
    await openDonors(
      { [donorsAt('min_traffic=5000000&geo=de&limit=20&offset=0')]: { body: nothing } },
      { path: '/donors?min_traffic=5000000&geo=de', ready: 'Под фильтр ничего не попало.' },
    );

    expect(
      screen.getByText(
        /^Условия: трафик не ниже 5\s000\s000, страна «Германия»\. Всего доноров — 3\.$/,
      ),
    ).toBeInTheDocument();
  });
});

describe('доноры: доноров нет вовсе', () => {
  it('путь туда, где доноров принимают, и сколько ждёт решения', async () => {
    await openDonors(
      {
        [donorsAt('limit=20&offset=0')]: {
          body: page({
            rows: [],
            total: 0,
            counts: {},
            countries: {},
            freshness: {},
            waiting: { domains: 394, runs: [24, 18] },
          }),
        },
      },
      { ready: 'Доноров пока нет.' },
    );

    expect(screen.getByText(/когда его принимает человек на рассмотрении прогона/)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Рассмотреть · 394' })).toHaveAttribute(
      'href',
      '/runs/24/review',
    );
    // Не «Доноров в базе нет»: проверенные домены в базе есть.
    expect(screen.queryByText(/в базе нет/)).toBeNull();
    // Выгружать нечего — кнопка выключена, а не приносит пустой файл.
    expect(screen.getByRole('button', { name: 'Выгрузить всех · 0' })).toBeDisabled();
  });
});
