/**
 * Отбор: фильтры под колонками, страницы по двадцать, всё — в адресе
 * (замечание 26.09.2026).
 *
 * Проверяется, что фильтр уходит на сервер параметром, а не сужает уже
 * пришедшее: страниц у отбора десятки, и фильтр по одной из них врал бы
 * числами. И что адрес — чужой ввод: незнакомое или невозможное на вкладке
 * значение не сужает, а не превращается в пустую таблицу без объяснения.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { SelectionCard, SelectionView } from '../api/types';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const MACHINE = {
  intent: 'sells_own',
  recommendation: 'reject' as const,
  decided_by: 'rule' as const,
  quote: 'Kaffee online kaufen',
  reason: 'продаёт своё',
  source_url: 'https://brand.test/ratgeber',
  home_shop: [],
  home_reached: true,
  judged_at: '2026-09-23T10:00:00+00:00',
};

const BRAND: SelectionCard = {
  domain_id: 7,
  host: 'brand.test',
  tab: 'rejected',
  donor_id: 70,
  status: 'suitable',
  reject_reason: null,
  dr: 55,
  org_traffic: 12000,
  machine: MACHINE,
  human: { intent: null, note: null, decided_at: null },
  seller: { answer: null, answered_at: null, price: null, currency: null },
  disagrees: false,
};

function view(rows: SelectionCard[], extra: Partial<SelectionView> = {}): SelectionView {
  return {
    rows,
    total: rows.length,
    page: 1,
    limit: 20,
    tabs: { accepted: 12, review: 2, rejected: 45 },
    reviewed: 4,
    disagreements: 1,
    layers: {},
    answered: 0,
    answer_layers: {},
    ...extra,
  };
}

const PAGE = view([BRAND]);

function at(query: string): string {
  return `GET /api/selection?${query}`;
}

async function openScreen(
  routes: Record<string, Answer> = {},
  { path = '/selection', ready = 'brand.test' }: { path?: string; ready?: string } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    [at('tab=accepted')]: { body: PAGE },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready);
  return recorded;
}

function asked(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/selection?'))
    .map((call: Call) => call.path.replace('/api/selection?', ''));
}

async function choose(field: string, option: RegExp | string) {
  const user = userEvent.setup();
  // Выпадающий список Mantine в jsdom остаётся `display: none` — раскладки
  // здесь нет, и без `hidden` его пункты не видны запросу. Клик настоящий.
  await user.click(screen.getByRole('textbox', { name: field }));
  await user.click(await screen.findByRole('option', { name: option, hidden: true }));
}

describe('отбор: фильтры под колонками', () => {
  it('над таблицей — только вкладки, фильтры — строкой под заголовками', async () => {
    await openScreen({ [at('tab=rejected')]: { body: PAGE } }, { path: '/selection?tab=rejected' });

    const head = screen.getByRole('columnheader', { name: 'Судья' }).closest('thead');
    if (head === null) throw new Error('шапки таблицы нет');
    for (const name of [
      'Поиск по домену или причине',
      'Вердикт порогов',
      'Кто вынес вердикт',
      'Ответ донора',
      'Решение человека',
    ]) {
      expect(within(head).getByRole('textbox', { name })).toBeInTheDocument();
    }
    // Четыре переключателя над таблицей ушли вместе с отдельной карточкой.
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('у принятых фильтра порогов нет: они все прошли пороги', async () => {
    await openScreen();

    expect(screen.queryByRole('textbox', { name: 'Вердикт порогов' })).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Кто вынес вердикт' })).toBeInTheDocument();
  });

  it('вкладка, фильтры и страница берутся из адреса', async () => {
    const query = 'tab=rejected&thresholds=none&judge=rule&answer=declines&human=disagrees&page=2';
    const recorded = await openScreen(
      { [at(query)]: { body: view([BRAND], { total: 25, page: 2 }) } },
      { path: `/selection?${query}` },
    );

    expect(asked(recorded)).toEqual([query]);
    expect(screen.getByRole('textbox', { name: 'Вердикт порогов' })).toHaveValue(
      'до Ahrefs не дошёл',
    );
    expect(screen.getByRole('textbox', { name: 'Кто вынес вердикт' })).toHaveValue('правило');
    expect(screen.getByRole('textbox', { name: 'Ответ донора' })).toHaveValue('не продаёт');
    expect(screen.getByRole('textbox', { name: 'Решение человека' })).toHaveValue(
      'разошёлся с судьёй',
    );
  });

  it('незнакомое значение в адресе не сужает, а не роняет экран', async () => {
    const recorded = await openScreen(
      {},
      { path: '/selection?tab=robots&judge=robot&human=maybe&page=-2&search=%20' },
    );

    expect(asked(recorded)).toEqual(['tab=accepted']);
    expect(screen.getByRole('textbox', { name: 'Кто вынес вердикт' })).toHaveValue('все');
  });

  it('значение, которого на вкладке не бывает, не сужает', async () => {
    // «Не подходит» лежит только в отклонённых, «не продаёт» — тоже:
    // отказ порогов и ответ донора сильнее всего остального.
    const recorded = await openScreen(
      { [at('tab=review')]: { body: PAGE } },
      { path: '/selection?tab=review&thresholds=unsuitable&answer=declines' },
    );

    expect(asked(recorded)).toEqual(['tab=review']);
    expect(screen.getByRole('textbox', { name: 'Вердикт порогов' })).toHaveValue('все');
  });

  it('фильтр уходит на сервер параметром и возвращает на первую страницу', async () => {
    const recorded = await openScreen(
      {
        [at('tab=rejected&page=3')]: { body: view([BRAND], { total: 45, page: 3 }) },
        [at('tab=rejected&judge=model')]: { body: PAGE },
      },
      { path: '/selection?tab=rejected&page=3' },
    );

    await choose('Кто вынес вердикт', /^модель/);

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('tab=rejected&judge=model'));
  });

  it('фильтр судьи называет слой словом значка и объясняет его в самом списке', async () => {
    await openScreen();
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Кто вынес вердикт' }));

    const rule = await screen.findByRole('option', { name: /^правило/, hidden: true });
    expect(rule).toHaveTextContent('правиловыдача и главная сказали одно');
    expect(screen.getByRole('option', { name: /^арбитр/, hidden: true })).toHaveTextContent(
      'выдача и главная спорили',
    );
    expect(screen.getByRole('option', { name: 'судья не смотрел', hidden: true })).toBeTruthy();
  });

  it('человек — один фильтр вместо двух переключателей', async () => {
    const recorded = await openScreen({
      [at('tab=accepted&human=unreviewed')]: { body: PAGE },
      [at('tab=accepted&human=disagrees')]: { body: PAGE },
    });

    await choose('Решение человека', 'не смотрел');
    await waitFor(() => expect(asked(recorded).at(-1)).toBe('tab=accepted&human=unreviewed'));
    await choose('Решение человека', 'разошёлся с судьёй');

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('tab=accepted&human=disagrees'));
  });

  it('поиск уходит в адрес и на сервер после паузы в наборе', async () => {
    const recorded = await openScreen({ [at('tab=accepted&search=brand')]: { body: PAGE } });
    const user = userEvent.setup();

    await user.type(screen.getByRole('textbox', { name: 'Поиск по домену или причине' }), 'brand');

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('tab=accepted&search=brand'));
    expect(asked(recorded).filter((query) => query.includes('search='))).toEqual([
      'tab=accepted&search=brand',
    ]);
  });
});

describe('отбор: страницы', () => {
  it('по двадцать: размер называет сервер, страница уходит номером', async () => {
    const recorded = await openScreen({
      [at('tab=accepted')]: { body: view([BRAND], { total: 45 }) },
      [at('tab=accepted&page=2')]: {
        body: view([{ ...BRAND, domain_id: 8, host: 'second.test' }], { total: 45, page: 2 }),
      },
    });
    const user = userEvent.setup();

    const pages = screen.getByRole('navigation', { name: 'Страницы отбора' });
    // 45 при двадцати на странице — три страницы, и номер — в своём элементе.
    expect(within(pages).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
    expect(pages.querySelector('[data-page-number]')).not.toBeNull();
    await user.click(within(pages).getByRole('button', { name: 'Страница 2' }));

    await screen.findByText('second.test');
    expect(asked(recorded)).toEqual(['tab=accepted', 'tab=accepted&page=2']);
  });

  it('страниц одна — переключателя нет', async () => {
    await openScreen();

    expect(screen.queryByRole('navigation', { name: 'Страницы отбора' })).toBeNull();
  });

  it('смена вкладки — с первой страницы', async () => {
    const recorded = await openScreen(
      {
        [at('tab=accepted&page=2')]: { body: view([BRAND], { total: 45, page: 2 }) },
        [at('tab=rejected')]: { body: view([{ ...BRAND, host: 'rejected.test' }]) },
      },
      { path: '/selection?page=2' },
    );
    const user = userEvent.setup();

    await user.click(screen.getByText('Отклонены — 45'));

    await screen.findByText('rejected.test');
    expect(asked(recorded).at(-1)).toBe('tab=rejected');
  });

  it('страница за концом — последняя настоящая, а не «ничего не нашлось»', async () => {
    const recorded = await openScreen(
      {
        [at('tab=accepted&page=9')]: { body: view([], { total: 45, page: 9 }) },
        [at('tab=accepted&page=3')]: { body: view([BRAND], { total: 45, page: 3 }) },
      },
      { path: '/selection?page=9' },
    );

    await waitFor(() => expect(asked(recorded).at(-1)).toBe('tab=accepted&page=3'));
    expect(screen.queryByText('Под фильтр ничего не попало.')).toBeNull();
  });
});

describe('отбор: пусто и отказ', () => {
  it('пусто под фильтром — условия словами и «Сбросить фильтры», вкладка остаётся', async () => {
    const recorded = await openScreen(
      {
        [at('tab=rejected&judge=arbiter&human=disagrees')]: { body: view([]) },
        [at('tab=rejected')]: { body: PAGE },
      },
      {
        path: '/selection?tab=rejected&judge=arbiter&human=disagrees',
        ready: 'Под фильтр ничего не попало.',
      },
    );
    const user = userEvent.setup();

    expect(
      screen.getByText(
        'Условия: вердикт вынес «арбитр», человек разошёлся с судьёй. На вкладке «Отклонены» всего — 45.',
      ),
    ).toBeInTheDocument();
    // Фильтры на месте: условие поправляют тут же, не возвращаясь.
    expect(screen.getByRole('textbox', { name: 'Кто вынес вердикт' })).toHaveValue('арбитр');

    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));

    await screen.findByText('brand.test');
    expect(asked(recorded).at(-1)).toBe('tab=rejected');
  });

  it('отказ сервера на новом фильтре — строкой в таблице; сводка и фильтры стоят', async () => {
    await openScreen({
      [at('tab=accepted&answer=answered')]: {
        status: 500,
        body: { detail: 'База отбора не ответила — повторите через минуту' },
      },
    });

    await choose('Ответ донора', 'ответил');

    expect(
      await screen.findByText('База отбора не ответила — повторите через минуту'),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Отбор' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Ответ донора' })).toHaveValue('ответил');
    expect(screen.getByText('Смотрел человек')).toBeInTheDocument();
  });
});
