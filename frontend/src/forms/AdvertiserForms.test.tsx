/**
 * «Формы» — вкладка рекламодателей (сверка ТЗ 11.10.2026: «только форма, нет email —
 * заполняем вручную» было только у доноров).
 *
 * Проверяется то, ради чего вкладка: видно, сколько ждёт в каждой очереди, у строки —
 * откуда рекламодатель и его балл, вписанный адрес уходит на сервер рекламодателей,
 * а «Не вышло» спрашивает подтверждения своими словами.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AdvertiserFormsView } from '../api/contacts';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call } from '../test/server';

const DONORS = {
  rows: [
    {
      donor_id: 7,
      domain_id: 70,
      host: 'form.example.test',
      dr: 55,
      org_traffic: 9000,
      attempted_at: '2026-09-20T10:00:00+00:00',
    },
  ],
  total: 1,
  page: 1,
  limit: 20,
  monthly_left: 98,
  monthly_cap: 100,
  advertisers_waiting: 2,
};

const ADVERTISERS: AdvertiserFormsView = {
  rows: [
    {
      advertiser_id: 11,
      domain_id: 110,
      host: 'shop.example.test',
      source: 'links',
      donor_host: 'donor.example.test',
      run_id: null,
      points: 7,
      attempted_at: '2026-10-01T10:00:00+00:00',
    },
    {
      advertiser_id: 12,
      domain_id: 120,
      host: 'casino.example.test',
      source: 'niche',
      donor_host: null,
      run_id: 12,
      points: 0,
      attempted_at: null,
    },
  ],
  total: 2,
  page: 1,
  limit: 20,
  monthly_left: 97,
  monthly_cap: 100,
  donors_waiting: 1,
  retry_days: 30,
};

/** Где сейчас экран: адрес целиком. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

function openForms(routes: Record<string, Answer> = {}, route = '/forms') {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts/forms?page=1': { body: DONORS },
    'GET /api/advertisers/forms?page=1': { body: ADVERTISERS },
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    route,
  );
  return recorded;
}

describe('формы: вкладка рекламодателей', () => {
  it('у вкладок — сколько ждёт в каждой очереди, без второго запроса', async () => {
    const recorded = openForms();

    await screen.findByText('form.example.test');
    expect(screen.getByText('Доноры · 1')).toBeInTheDocument();
    expect(screen.getByText('Рекламодатели · 2')).toBeInTheDocument();
    expect(recorded.calls.some((call: Call) => call.path.startsWith('/api/advertisers'))).toBe(
      false,
    );
  });

  it('вкладка — в адресе; строка — откуда рекламодатель и его балл', async () => {
    openForms();
    const user = userEvent.setup();
    await screen.findByText('form.example.test');

    await user.click(screen.getByText('Рекламодатели · 2'));

    const row = (await screen.findByText('shop.example.test')).closest('tr')!;
    expect(screen.getByTestId('where')).toHaveTextContent('/forms?queue=advertisers');
    expect(within(row).getByText('donor.example.test')).toBeInTheDocument();
    expect(within(row).getByText('7')).toBeInTheDocument();
    const niche = screen.getByText('casino.example.test').closest('tr')!;
    expect(within(niche).getByText('бизнес ниши, прогон №12')).toBeInTheDocument();
    // Балла у бизнеса ниши нет, и искали его ещё не по сроку: прочерки, а не «0».
    expect(within(niche).getAllByText('—')).toHaveLength(2);
    // Ориентир — тот же, что у доноров: один на обе очереди.
    expect(screen.getByText(/Ориентир темпа/)).toHaveTextContent(
      'В очереди 2. Ориентир темпа — 100 адресов руками за последние 30 дней, осталось 97.',
    );
    const labels = [...row.querySelectorAll('td[data-label]')].map((cell) =>
      cell.getAttribute('data-label'),
    );
    expect(labels).toEqual(['Донор', 'Балл', 'Искали']);
  });

  it('вписанный адрес уходит на сервер рекламодателей', async () => {
    const recorded = openForms(
      { 'POST /api/advertisers/forms/11/filled': { body: ADVERTISERS.rows[0] } },
      '/forms?queue=advertisers',
    );
    const user = userEvent.setup();
    await screen.findByText('shop.example.test');

    await user.click(screen.getAllByRole('button', { name: 'Вписать адрес' })[0]!);
    const dialog = await screen.findByRole('dialog', {
      name: 'Адрес рекламодателя shop.example.test',
    });
    await user.type(within(dialog).getByLabelText('Адрес почты'), 'ads@shop.example.test');
    await user.click(within(dialog).getByRole('button', { name: 'Записать' }));

    expect(
      await screen.findByText('shop.example.test: адрес записан, ему уйдёт оффер'),
    ).toBeInTheDocument();
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent).toEqual([
      expect.objectContaining({
        path: '/api/advertisers/forms/11/filled',
        body: { email: 'ads@shop.example.test' },
      }),
    ]);
  });

  it('«Не вышло» — с подтверждением своими словами', async () => {
    const recorded = openForms(
      { 'POST /api/advertisers/forms/11/give-up': { body: ADVERTISERS.rows[0] } },
      '/forms?queue=advertisers',
    );
    const user = userEvent.setup();
    await screen.findByText('shop.example.test');

    await user.click(screen.getAllByRole('button', { name: 'Не вышло' })[0]!);
    const ask = await screen.findByRole('dialog', {
      // Тем, что сделает код: «Не вышло» не навсегда — срок годности вернёт его в поиск.
      name:
        'Закрыть shop.example.test без адреса? Рекламодатель уйдёт из очереди форм; адрес ему ' +
        'поищут снова через 30 дней после прошлого поиска — до этого оффера не будет.',
      hidden: true,
    });
    await user.click(within(ask).getByRole('button', { name: 'Закрыть', hidden: true }));

    await screen.findByText('shop.example.test закрыт без адреса');
    expect(
      recorded.calls.some((call: Call) => call.path === '/api/advertisers/forms/11/give-up'),
    ).toBe(true);
  });

  it('кого здесь нет — в «i» у заголовка: стоп-лист и другое направление', async () => {
    // 28.10.2026: форма — тоже письмо, и очередь рекламодателей судит их правилом отправки.
    openForms({}, '/forms?queue=advertisers');
    const user = userEvent.setup();
    await screen.findByText('shop.example.test');

    await user.hover(screen.getByRole('button', { name: 'Как устроен экран «Формы»' }));

    expect(
      await screen.findByText(
        /Сайта в стоп-листе или в работе у другого направления здесь нет: форма — тоже письмо/,
      ),
    ).toBeInTheDocument();
  });

  it('очередь рекламодателей пуста — кто сюда попадает', async () => {
    openForms(
      {
        'GET /api/advertisers/forms?page=1': {
          body: { ...ADVERTISERS, rows: [], total: 0 },
        },
      },
      '/forms?queue=advertisers',
    );

    expect(
      await screen.findByText(
        'Очередь пуста. Сюда попадают рекламодатели, которым пишем, — у них лестница нашла форму, но не нашла адреса.',
      ),
    ).toBeInTheDocument();
  });
});
