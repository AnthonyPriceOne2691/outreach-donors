/**
 * Карточка донора: адреса руками, на какой уйдёт письмо, кто это — донор
 * или кандидат, и цена общими деньгами.
 *
 * Замечание 26.09.2026: «проверь, чтобы в карточке донора была возможность
 * заполнить контакты, и не один ящик, а несколько». Проверяется, что адрес
 * уходит на сервер и карточка берёт ответ сервера целиком (адрес меняет
 * исход поиска и адрес письма), что отказ виден словами сервера, что адрес
 * с перепиской не удаляется и почему — видно до нажатия. И что шапка не
 * называет донором того, кого человек не принимал.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { ContactCard, DonorFullCard, Me } from '../api/types';
import { CARD } from '../test/donorFixtures';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

function contact(id: number, email: string, fields: Partial<ContactCard> = {}): ContactCard {
  return {
    id,
    email,
    source: 'page',
    last_contacted_at: null,
    last_replied_at: null,
    removal_refusal: null,
    ...fields,
  };
}

async function openCard(
  card: DonorFullCard,
  routes: Record<string, Answer> = {},
  me: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: me },
    [`GET /api/donors/${card.id}`]: { body: card },
    ...routes,
  });
  renderWith(<AppRoutes />, `/donors/${card.id}`);
  await screen.findByRole('heading', { name: card.host });
  return recorded;
}

function addresses(): HTMLElement {
  return screen.getByRole('heading', { name: 'Адреса' }).closest('.mantine-Card-root')!;
}

function rowOf(email: string): HTMLElement {
  return within(addresses()).getByText(email).closest('tr')!;
}

describe('карточка донора: адрес руками', () => {
  it('вписанный адрес уходит на сервер, и карточка — из ответа сервера', async () => {
    const written = contact(31, 'editor@card.example.test', { source: 'manual' });
    const recorded = await openCard(CARD, {
      'POST /api/contacts/donors/7/addresses': {
        body: { ...CARD, contact_status: 'found', contacts: [written], letter_contact_id: 31 },
      },
    });
    const user = userEvent.setup();

    await user.type(
      screen.getByRole('textbox', { name: 'Новый адрес почты' }),
      'Editor@card.example.test',
    );
    await user.click(screen.getByRole('button', { name: 'Добавить адрес' }));

    expect(await within(addresses()).findByText('editor@card.example.test')).toBeVisible();
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => [call.path, call.body])).toEqual([
      ['/api/contacts/donors/7/addresses', { email: 'Editor@card.example.test' }],
    ]);
    expect(within(addresses()).getByText('адрес найден')).toBeInTheDocument();
    // Поиска не было — «Искали <дата>» здесь была бы выдумкой.
    expect(
      within(addresses()).getByText('Адрес вписан вручную, лестницей не искали.'),
    ).toBeVisible();
    expect(within(rowOf('editor@card.example.test')).getByText('вписан вручную')).toBeVisible();
    // Поле очищено: следующий адрес вписывают с чистого.
    expect(screen.getByRole('textbox', { name: 'Новый адрес почты' })).toHaveValue('');
  });

  it('отказ сервера — под полем его словами, и поле не очищается', async () => {
    const refusal = 'Адрес ads@card.example.test у донора уже есть.';
    await openCard(CARD, {
      'POST /api/contacts/donors/7/addresses': { status: 409, body: { detail: refusal } },
    });
    const user = userEvent.setup();

    await user.type(
      screen.getByRole('textbox', { name: 'Новый адрес почты' }),
      'ads@card.example.test',
    );
    await user.click(screen.getByRole('button', { name: 'Добавить адрес' }));

    expect(await screen.findByText(refusal)).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Новый адрес почты' })).toHaveValue(
      'ads@card.example.test',
    );
  });

  it('без @ — кнопка заперта: сервер всё равно отказал бы', async () => {
    await openCard(CARD);
    const user = userEvent.setup();

    await user.type(screen.getByRole('textbox', { name: 'Новый адрес почты' }), 'editor');

    expect(screen.getByRole('button', { name: 'Добавить адрес' })).toBeDisabled();
  });

  it('без права — объяснение на месте, а не поле и не кнопки', async () => {
    await openCard(
      { ...CARD, contacts: [contact(1, 'ads@card.example.test')] },
      {},
      { ...OPERATOR, permissions: ['view'] },
    );

    expect(
      within(addresses()).getByText(
        'Вписывать и удалять адреса может сотрудник с правом «запускать прогоны».',
      ),
    ).toBeVisible();
    expect(screen.queryByRole('textbox', { name: 'Новый адрес почты' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Удалить/ })).toBeNull();
  });
});

describe('карточка донора: удаление адреса', () => {
  it('удаление — вторым нажатием, и карточка — из ответа сервера', async () => {
    const card = {
      ...CARD,
      contact_status: 'found' as const,
      contact_attempted_at: '2026-09-21T10:00:00+00:00',
      contacts: [contact(1, 'old@card.example.test'), contact(2, 'ads@card.example.test')],
      letter_contact_id: 1,
    };
    const recorded = await openCard(card, {
      'DELETE /api/contacts/donors/7/addresses/1': {
        body: { ...card, contacts: [card.contacts[1]!], letter_contact_id: 2 },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Удалить old@card.example.test' }));
    // Первое нажатие только спрашивает: адрес платной ступени одним промахом
    // не теряется.
    expect(recorded.calls.some((call) => call.method === 'DELETE')).toBe(false);
    await user.click(await screen.findByRole('button', { name: 'Удалить' }));

    await waitFor(() =>
      expect(within(addresses()).queryByText('old@card.example.test')).toBeNull(),
    );
    expect(
      recorded.calls.filter((call) => call.method === 'DELETE').map((call) => call.path),
    ).toEqual(['/api/contacts/donors/7/addresses/1']);
  });

  it('адрес с перепиской не удаляется, и почему — видно до нажатия', async () => {
    const refusal =
      'По этому адресу уже есть письма — удалить нельзя: переписка потеряла бы адресата.';
    await openCard({
      ...CARD,
      contacts: [
        contact(1, 'talked@card.example.test', {
          last_contacted_at: '2026-09-20T10:00:00+00:00',
          removal_refusal: refusal,
        }),
      ],
    });
    const user = userEvent.setup();

    expect(screen.queryByRole('button', { name: 'Удалить talked@card.example.test' })).toBeNull();
    await user.click(
      screen.getByRole('button', { name: 'Почему не удалить talked@card.example.test' }),
    );

    // Поповер в jsdom без раскладки: проверяется, что причина в нём есть.
    expect(await screen.findByRole('dialog')).toHaveTextContent(refusal);
  });
});

describe('карточка донора: на какой адрес уйдёт письмо', () => {
  it('при нескольких адресах отмечен тот, что выбрал сервер, — и только он', async () => {
    await openCard({
      ...CARD,
      contacts: [
        contact(1, 'editor@card.example.test', { source: 'manual' }),
        contact(2, 'ads@card.example.test'),
      ],
      letter_contact_id: 1,
    });

    expect(within(rowOf('editor@card.example.test')).getByText('письмо уйдёт сюда')).toBeVisible();
    expect(within(rowOf('ads@card.example.test')).queryByText('письмо уйдёт сюда')).toBeNull();
  });

  it('один адрес — отметка не нужна', async () => {
    await openCard({
      ...CARD,
      contacts: [contact(1, 'ads@card.example.test')],
      letter_contact_id: 1,
    });

    expect(screen.queryByText('письмо уйдёт сюда')).toBeNull();
  });

  it('письмо не соберётся ни на один — сказано почему, словами сборки', async () => {
    await openCard({
      ...CARD,
      contacts: [contact(1, 'ads@card.example.test'), contact(2, 'info@card.example.test')],
      letter_contact_id: null,
      letter_blocked: 'донору уже писали — следующие письма идут в тот же диалог',
    });

    expect(
      within(addresses()).getByText('Донору уже писали — следующие письма идут в тот же диалог.'),
    ).toBeVisible();
    expect(screen.queryByText('письмо уйдёт сюда')).toBeNull();
  });
});

describe('карточка донора: донор или кандидат', () => {
  it('принятый — донор, лишних слов нет', async () => {
    await openCard(CARD);

    expect(screen.queryByText(/не донор/i)).toBeNull();
  });

  it('без решения — кандидат, и ссылка туда, где решают', async () => {
    await openCard({ ...CARD, review: null, review_run: 18 });

    expect(screen.getByText(/^Кандидат, а не донор: ждёт решения человека/)).toBeVisible();
    expect(screen.getByRole('link', { name: 'в очереди прогона №18' })).toHaveAttribute(
      'href',
      '/runs/18/review',
    );
  });

  it('отклонённый — не донор, и где снять решение', async () => {
    await openCard({ ...CARD, review: 'rejected', review_run: 21 });

    expect(screen.getByText(/^Не донор: отклонён человеком/)).toHaveTextContent(
      'Не донор: отклонён человеком в очереди прогона №21. Решение можно снять там же.',
    );
  });

  it('ни решения, ни очереди — так и сказано', async () => {
    await openCard({ ...CARD, review: null, review_run: null });

    expect(screen.getByText(/в очередях прогонов он не стоит/)).toBeVisible();
  });
});

describe('карточка донора: цена', () => {
  it('последняя цена — общими деньгами, а не сырой строкой сервера', async () => {
    await openCard({
      ...CARD,
      last_price: '1250.00',
      last_price_currency: 'EUR',
      last_price_at: '2026-09-20T10:00:00+00:00',
    });

    const price = screen
      .getByRole('heading', { name: 'Последняя цена' })
      .closest('.mantine-Card-root')!;
    expect(price.textContent?.replace(/[\u00a0\u202f]/g, ' ')).toContain('1 250,00 € · 20.09.2026');
    expect(price.textContent).not.toContain('1250.00');
  });
});
