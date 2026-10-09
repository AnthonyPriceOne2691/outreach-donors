/**
 * Ручная очередь форм.
 *
 * Ступень, которую нельзя пройти кодом: у донора есть форма и нет
 * почты. До этого экрана очередь существовала значком в общей таблице
 * и больше нигде.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Call } from '../test/server';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const QUEUE = {
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
};

async function openForms(
  routes: Record<string, unknown> = {},
  who: unknown = ADMIN,
  route = '/forms',
  first = 'form.example.test',
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/contacts/forms?page=1': { body: QUEUE },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, route);
  await screen.findByText(first);
  return recorded;
}

/** Страница очереди из `count` доноров, номера — с `from`. */
function page(count: number, { from = 1, total = count, number = 1 } = {}) {
  return {
    body: {
      ...QUEUE,
      rows: Array.from({ length: count }, (_, at) => ({
        ...QUEUE.rows[0],
        donor_id: from + at,
        domain_id: 100 + from + at,
        host: `form-${from + at}.example.test`,
      })),
      total,
      page: number,
    },
  };
}

describe('ручная очередь форм', () => {
  it('остаток месячного потолка виден числом', async () => {
    await openForms();

    // Человек, разбирающий пачку, должен видеть, сколько ещё можно
    // взять: без потолка очередь никто никогда не разберёт.
    expect(screen.getByText(/98/)).toBeInTheDocument();
  });

  it('вписанный адрес уходит на сервер', async () => {
    const recorded = await openForms({
      'POST /api/contacts/forms/7/filled': { body: { ...QUEUE.rows[0] } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Вписать адрес' }));
    await user.type(await screen.findByLabelText('Адрес почты'), 'editor@form.example.test');
    await user.click(screen.getByRole('button', { name: 'Записать' }));

    await screen.findByText(/адрес записан/);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ email: 'editor@form.example.test' });
  });

  it('без адреса кнопка записи заперта', async () => {
    await openForms();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Вписать адрес' }));

    expect(await screen.findByRole('button', { name: 'Записать' })).toBeDisabled();
  });

  it('«не вышло» закрывает строку', async () => {
    const recorded = await openForms({
      'POST /api/contacts/forms/7/give-up': { body: { ...QUEUE.rows[0] } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не вышло' }));
    // Закрывает насовсем — с подтверждением у кнопки (09.10.2026); до него ничего не ушло.
    const ask = await screen.findByRole('dialog', { name: /^Закрыть .* без адреса/, hidden: true });
    expect(recorded.calls.some((call: Call) => call.path.endsWith('/give-up'))).toBe(false);
    await user.click(within(ask).getByRole('button', { name: 'Закрыть', hidden: true }));

    await screen.findByText(/закрыт без адреса/);
    expect(recorded.calls.some((call: Call) => call.path.endsWith('/give-up'))).toBe(true);
  });

  it('оператор без права видит очередь, но не правит её', async () => {
    await openForms({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(screen.getByText('form.example.test')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Вписать адрес' })).not.toBeInTheDocument();
  });
});

describe('очередь форм: колонки и страницы', () => {
  // Замечание 28.09.2026: «добавить в последнюю колонку шапку „Действия“,
  // пагинация, 20 записей на странице».
  const PAGER = 'Страницы очереди форм';

  it('колонка кнопок подписана «Действия»', async () => {
    await openForms();

    expect(screen.getByRole('columnheader', { name: 'Действия' })).toBeInTheDocument();
  });

  it('без права работать с очередью нет ни кнопок, ни их колонки', async () => {
    await openForms({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(screen.queryByRole('columnheader', { name: 'Действия' })).not.toBeInTheDocument();
  });

  it('длинный домен донора переносится по швам, а не посреди слова', async () => {
    // Замечание 06.10.2026: в колонке «Донор» длинный домен рвался там, где
    // кончилось место, — «analysis.example.te / st». Остальные колонки «Донор»
    // переносят его по швам с того же дня, «Формы» оставались в стороне.
    const host = 'gambling-news-and-analysis.example.test';
    await openForms(
      {
        'GET /api/contacts/forms?page=1': {
          body: { ...QUEUE, rows: [{ ...QUEUE.rows[0], host }] },
        },
      },
      ADMIN,
      '/forms',
      host,
    );

    const name = screen.getByRole('link', { name: host });
    // Имя то же: его копируют и ищут по странице, шов — место переноса, а не знак.
    expect(name).toHaveTextContent(host);
    expect(name.querySelectorAll('wbr')).toHaveLength(2);
    expect(name).toHaveClass('cellName');
  });

  it('одна страница — переключателя нет вовсе', async () => {
    await openForms();

    expect(screen.queryByRole('navigation', { name: PAGER, hidden: true })).not.toBeInTheDocument();
  });

  it('больше двадцати — переключатель, и вторая страница спрашивается у сервера', async () => {
    const recorded = await openForms(
      {
        'GET /api/contacts/forms?page=1': page(20, { total: 45 }),
        'GET /api/contacts/forms?page=2': page(20, { from: 21, total: 45, number: 2 }),
      },
      ADMIN,
      '/forms',
      'form-1.example.test',
    );
    const user = userEvent.setup();

    const pager = screen.getByRole('navigation', { name: PAGER });
    // Страниц три — по размеру, который назвал сервер, а не по своему числу.
    expect(within(pager).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
    await user.click(within(pager).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('form-21.example.test')).toBeInTheDocument();
    expect(screen.queryByText('form-1.example.test')).not.toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path === '/api/contacts/forms?page=2')).toBe(true);
    // Экран не режет список сам: на странице ровно то, что прислал сервер.
    expect(screen.getAllByRole('row')).toHaveLength(1 + 20);
  });

  it('номер страницы — в адресе: вторая по ссылке открывается второй', async () => {
    const recorded = await openForms(
      { 'GET /api/contacts/forms?page=2': page(5, { from: 21, total: 25, number: 2 }) },
      ADMIN,
      '/forms?page=2',
      'form-21.example.test',
    );

    expect(recorded.calls.some((call) => call.path === '/api/contacts/forms?page=1')).toBe(false);
    const pager = screen.getByRole('navigation', { name: PAGER });
    expect(within(pager).getByRole('button', { name: 'Страница 2' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('страница за концом уводит на последнюю, а не показывает «очередь пуста»', async () => {
    // Последнего донора последней страницы закрыли — или ссылку открыли после
    // разбора очереди.
    const recorded = await openForms(
      {
        'GET /api/contacts/forms?page=4': page(0, { total: 45, number: 4 }),
        'GET /api/contacts/forms?page=3': page(5, { from: 41, total: 45, number: 3 }),
      },
      ADMIN,
      '/forms?page=4',
      'form-41.example.test',
    );

    expect(recorded.calls.map((call) => call.path)).toContain('/api/contacts/forms?page=3');
    expect(screen.queryByText(/Очередь пуста/)).not.toBeInTheDocument();
  });
});
