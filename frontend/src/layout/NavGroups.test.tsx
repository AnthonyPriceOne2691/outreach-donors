/**
 * Меню тремя группами — «Доноры и рекламодатели», «Продажи», «Настройки» (просьба
 * Anthony 11.10.2026: доноры и рекламодатели зависят друг от друга, продажи — отдельный
 * продукт, и меню это показывает).
 *
 * Проверяется: состав и порядок групп; группа продаж — только с правом; части общих
 * экранов — тем же путём с `?stage=sales`; подсвечен пункт открытой части; число
 * ждущей работы — у своего пункта; место в разделе помнится у каждого пункта своё.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Me } from '../api/types';
import { ADMIN, HOME_ROUTES, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { threadsPlace } from '../threads/threadTabs';

function thread(id: number, host: string, stage: string, state: string) {
  return {
    id,
    host,
    contact_email: `hi@${host}`,
    campaign: 'Демонстрация',
    stage,
    state,
    messages_sent: 1,
    last_event_at: '2026-10-11T10:00:00+00:00',
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  };
}

const THREADS = [
  thread(3, 'donor.example.test', 'donors', 'needs_review'),
  thread(7, 'lead.example.test', 'sales', 'sales_pending'),
];

/** Переписка без событий — по строке списка. */
function view(card: ReturnType<typeof thread>) {
  return { card, letters: [], incoming: [], corridor: { min: 0.15, max: 0.25 } };
}

const SCREENS = {
  ...HOME_ROUTES,
  'GET /api/threads': { body: THREADS },
  ...Object.fromEntries(
    THREADS.map((card) => [`GET /api/threads/${card.id}`, { body: view(card) }]),
  ),
  'GET /api/replies/calibration': { body: { versions: [] } },
  'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
};

/** Где сейчас экран: адрес целиком. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

/** Переход по ссылке извне — так ведут уведомления продаж (`/threads/{id}` без части). */
function Go() {
  const navigate = useNavigate();
  const [params] = useState(() => ({ to: '' }));
  return (
    <input
      aria-label="перейти по ссылке"
      onChange={(event) => {
        params.to = event.currentTarget.value;
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') void navigate(params.to);
      }}
    />
  );
}

/** Экран по адресу — когда рама с меню уже стоит. */
async function open(path: string, me: Me = ADMIN, extra: Parameters<typeof serve>[0] = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({ 'GET /api/auth/me': { body: me }, ...SCREENS, ...extra });
  renderWith(
    <>
      <AppRoutes />
      <Where />
      <Go />
    </>,
    path,
  );
  await screen.findByRole('navigation', { name: 'Разделы' });
}

/** Открыть ссылку извне, как уведомление. */
async function follow(to: string) {
  await userEvent.setup().type(screen.getByLabelText('перейти по ссылке'), `${to}{Enter}`);
}

const menu = () => within(screen.getByRole('navigation', { name: 'Разделы' }));

/** Пункты группы — подписями, по порядку. */
async function items(group: string): Promise<string[]> {
  const found = await menu().findByRole('group', { name: group });
  return within(found)
    .getAllByRole('link')
    .map((link) => link.textContent ?? '');
}

/** Ссылка пункта по имени; строкой — точно: «Диалоги» — не «Диалоги продаж». */
const link = (name: string | RegExp) => menu().getByRole('link', { name });

describe('меню группами', () => {
  it('три группы, порядок внутри прежний, журнал действий — рядом с учётками', async () => {
    await open('/');

    expect(await items('Доноры и рекламодатели')).toEqual([
      'Обзор',
      'Прогон',
      'Доноры',
      'Отбор',
      'Формы',
      'Рекламодатели',
      'Письма',
      'Диалоги',
    ]);
    expect(await items('Продажи')).toEqual([
      'Продажи',
      'Диалоги продаж',
      'Агент продаж',
      'Домены продаж',
    ]);
    expect(await items('Настройки')).toEqual([
      'Стоп-лист',
      'Пороги',
      'Агент переписки',
      'Расход',
      'Домены рассылки',
      'Учётки',
      'Журнал действий',
    ]);
  });

  it('без права «Продажи» группы продаж нет вовсе — ни подписи, ни пунктов', async () => {
    const noSales: Me = { ...OPERATOR, permissions: ['prices', 'run', 'settings', 'view'] };
    await open('/', noSales);

    await menu().findByRole('group', { name: 'Доноры и рекламодатели' });
    expect(menu().queryByRole('group', { name: 'Продажи' })).not.toBeInTheDocument();
    expect(menu().queryByRole('link', { name: 'Диалоги продаж' })).not.toBeInTheDocument();
  });

  it('части продаж — те же экраны с ?stage=sales', async () => {
    await open('/');

    await menu().findByRole('group', { name: 'Продажи' });
    expect(link('Продажи')).toHaveAttribute('href', '/sales');
    expect(link('Диалоги продаж')).toHaveAttribute('href', '/threads?stage=sales');
    expect(link('Агент продаж')).toHaveAttribute('href', '/agent?stage=sales');
    expect(link('Домены продаж')).toHaveAttribute('href', '/senders?stage=sales');
    expect(link('Диалоги')).toHaveAttribute('href', '/threads');
  });

  it('подсвечен пункт открытой части: «Диалоги» и «Диалоги продаж» — один путь', async () => {
    await open('/threads?stage=sales');

    await screen.findByRole('heading', { name: 'Диалоги продаж' });
    expect(link('Диалоги продаж')).toHaveAttribute('data-active');
    expect(link('Диалоги')).not.toHaveAttribute('data-active');
  });

  it('без части — «Диалоги», и в списке нет переписки продаж', async () => {
    await open('/threads');

    await screen.findByRole('heading', { name: 'Диалоги' });
    expect(link('Диалоги')).toHaveAttribute('data-active');
    expect(link('Диалоги продаж')).not.toHaveAttribute('data-active');
    expect(await screen.findByText('donor.example.test')).toBeInTheDocument();
    expect(screen.queryByText('lead.example.test')).not.toBeInTheDocument();
  });

  it('число ждущей работы — у своего пункта: продажи не в «Диалогах»', async () => {
    await open('/', ADMIN, {
      'GET /api/overview/work': {
        body: { run: 0, threads: 2, forms: 0, advertisers: 0, sales_threads: 5 },
      },
    });

    await waitFor(() => expect(link(/^Диалоги продаж/)).toHaveTextContent('5 ждут человека'));
    expect(menu().getByRole('link', { name: /^Диалоги(?! продаж)/ })).toHaveTextContent(
      '2 ждут человека',
    );
  });
});

describe('место в разделе — у каждого пункта своё', () => {
  it('«Диалоги» возвращают к диалогу доноров, «Диалоги продаж» — к переписке продаж', async () => {
    const user = userEvent.setup();
    await open('/threads?state=needs_review');
    await screen.findByText('donor.example.test');
    await user.click(screen.getByText('donor.example.test'));
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/threads/3?state=needs_review'),
    );

    await user.click(link('Диалоги продаж'));
    await user.click(await screen.findByText('lead.example.test'));
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/threads/7?stage=sales'),
    );
    await user.click(link('Обзор'));

    expect(link('Диалоги')).toHaveAttribute('href', '/threads/3?state=needs_review');
    expect(link('Диалоги продаж')).toHaveAttribute('href', '/threads/7?stage=sales');
  });

  it('ссылка без части на переписку продаж — «Диалоги» помнят свой диалог, а не её', async () => {
    // Проверка ветки 12.10.2026: адрес `/threads/7` сперва доставался «Диалогам», потом
    // переписка дописывала `?stage=sales` — и «Диалоги» уводили в продажи.
    const user = userEvent.setup();
    await open('/threads/3?state=needs_review');
    await screen.findByRole('heading', { name: 'donor.example.test' });

    await follow('/threads/7');
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/threads/7?stage=sales'),
    );
    await user.click(link('Обзор'));

    expect(link('Диалоги')).toHaveAttribute('href', '/threads/3?state=needs_review');
    expect(link('Диалоги продаж')).toHaveAttribute('href', '/threads/7?stage=sales');
    // Память вкладки «Диалоги» у части доноров — тоже своя.
    expect(threadsPlace(null)).toBe('/threads/3?state=needs_review');
    expect(threadsPlace('sales')).toBe('/threads/7?stage=sales');
  });

  it('зеркально: переписка донора под частью продаж — «Диалоги продаж» помнят свою', async () => {
    const user = userEvent.setup();
    await open('/threads/7?stage=sales');
    await screen.findByRole('heading', { name: 'lead.example.test' });

    await follow('/threads/3?stage=sales');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/threads\/3$/));
    await user.click(link('Обзор'));

    expect(link('Диалоги продаж')).toHaveAttribute('href', '/threads/7?stage=sales');
    expect(link('Диалоги')).toHaveAttribute('href', '/threads/3');
    expect(threadsPlace('sales')).toBe('/threads/7?stage=sales');
  });
});

describe('свёрнутое меню держит границы групп', () => {
  const before = Object.getOwnPropertyDescriptor(window, 'matchMedia');
  afterEach(() => {
    if (before !== undefined) Object.defineProperty(window, 'matchMedia', before);
  });

  it('три группы — линиями, подписи — диктору, пункты групп на месте', async () => {
    // Колонка рядом с содержимым — на широком окне: совпадает только этот запрос.
    window.matchMedia = (query: string) =>
      ({
        matches: query === '(min-width: 48em)',
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList;
    await open('/');

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Свернуть меню' }));

    const groups = menu().getAllByRole('group');
    expect(groups.map((group) => group.getAttribute('aria-labelledby'))).toEqual([
      'nav-group-mail',
      'nav-group-sales',
      'nav-group-settings',
    ]);
    for (const group of groups) {
      // Подпись свёрнута в линию (`data-folded`), а имя группы диктору — на месте.
      expect(group.querySelector('.navGroupTitle')).toHaveAttribute('data-folded');
    }
    expect(within(groups[1]!).getByRole('link', { name: 'Диалоги продаж' })).toHaveAttribute(
      'href',
      '/threads?stage=sales',
    );
  });
});
