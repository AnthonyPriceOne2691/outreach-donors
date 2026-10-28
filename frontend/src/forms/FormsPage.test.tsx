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
  it('на телефоне строка — карточкой: решение под доменом, у значений подписи', async () => {
    await openForms();

    // Вид карточками задают стили уже 48em (`phoneCards`); тест держит разметку, на
    // которую они опираются: ячейка решения помечена, у значений — подписи колонок.
    const decide = screen.getByRole('button', { name: 'Вписать адрес' }).closest('td')!;
    expect(decide).toHaveClass('cellDecide');
    expect(decide.closest('.phoneCards')).not.toBeNull();
    const labels = [...decide.closest('tr')!.querySelectorAll('td[data-label]')];
    expect(labels.map((cell) => cell.getAttribute('data-label'))).toEqual([
      'DR',
      'Трафик',
      'Искали',
    ]);
  });

  it('потолок — ориентир темпа за последние 30 дней, остаток числом', async () => {
    await openForms();

    // Решение Anthony 11.10.2026: считаются адреса, вписанные руками, окном в 30 дней,
    // а не календарным месяцем, — и это ориентир, а не запрет.
    expect(screen.getByText(/Ориентир темпа/)).toHaveTextContent(
      'В очереди 1. Ориентир темпа — 100 адресов руками за последние 30 дней, осталось 98.',
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('ориентир достигнут — очередь не закрыта, а не «до следующего месяца»', async () => {
    // Потолок ничего не держит: форма сверх него — та же строка очереди. Экран обещал
    // «Новые формы в очередь не попадут до следующего месяца» — неправду.
    await openForms({ 'GET /api/contacts/forms?page=1': { body: { ...QUEUE, monthly_left: 0 } } });

    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('Ориентир темпа достигнут')).toBeInTheDocument();
    expect(alert).toHaveTextContent(
      'Это ориентир, а не запрет: очередь не закрыта, новые формы в неё попадают, вписывать адреса можно и дальше.',
    );
    expect(screen.queryByText(/следующего месяца/)).not.toBeInTheDocument();
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
    // Закрывает — с подтверждением у кнопки (09.10.2026); до него ничего не ушло. Сервер
    // без числа дней (старше экрана) — срок словами, а не обещание «письма не будет».
    const ask = await screen.findByRole('dialog', {
      name: 'Закрыть form.example.test без адреса? Донор уйдёт из очереди форм; адрес ему поищут снова по сроку годности прошлого поиска — до этого письма не будет.',
      hidden: true,
    });
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

  it('кого здесь нет — в «i» у заголовка: стоп-лист, «кому не пишем», другое направление', async () => {
    await openForms();
    const user = userEvent.setup();

    await user.hover(screen.getByRole('button', { name: 'Как устроен экран «Формы»' }));

    expect(
      await screen.findByText(
        /Сайта в стоп-листе, в «кому не пишем» или в работе у другого направления здесь нет/,
      ),
    ).toBeInTheDocument();
  });

  it('очередь пуста — сюда попадают доноры, которым пишем', async () => {
    // 28.10.2026: донора в стоп-листе и сайт в работе у другого направления очередь не берёт.
    const empty =
      'Очередь пуста. Сюда попадают доноры, которым пишем, — у них лестница нашла форму, но не нашла адреса.';
    await openForms(
      { 'GET /api/contacts/forms?page=1': { body: { ...QUEUE, rows: [], total: 0 } } },
      ADMIN,
      '/forms',
      empty,
    );

    expect(screen.getByText(empty)).toBeInTheDocument();
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
