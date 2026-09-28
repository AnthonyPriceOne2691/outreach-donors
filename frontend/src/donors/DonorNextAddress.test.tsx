/**
 * Карточка донора после отказа доставки: мёртвый адрес помечен, письмо
 * отмечено на следующем, и почему не на первом — словами сервера.
 *
 * До 28.09.2026 сборка после отказа не писала донору вовсе, а карточка
 * отмечала мёртвый адрес как лучший. Здесь проверяется только экран:
 * что он показывает то, что решил сервер (`Recipients.letter_address`),
 * и не додумывает своего.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { ContactCard, DonorFullCard } from '../api/types';
import { CARD } from '../test/donorFixtures';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

function contact(id: number, email: string, fields: Partial<ContactCard> = {}): ContactCard {
  return {
    id,
    email,
    source: 'page',
    last_contacted_at: null,
    last_replied_at: null,
    removal_refusal: null,
    bounced: false,
    ...fields,
  };
}

async function openCard(card: DonorFullCard): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    [`GET /api/donors/${card.id}`]: { body: card },
  });
  renderWith(<AppRoutes />, `/donors/${card.id}`);
  await screen.findByRole('heading', { name: card.host });
}

function addresses(): HTMLElement {
  return screen.getByRole('heading', { name: 'Адреса' }).closest('.mantine-Card-root')!;
}

function rowOf(email: string): HTMLElement {
  return within(addresses()).getByText(email).closest('tr')!;
}

const WRITTEN = '2026-09-27T10:00:00+00:00';

describe('карточка донора: следующий адрес', () => {
  it('мёртвый помечен, письмо отмечено на следующем, и сказано почему', async () => {
    await openCard({
      ...CARD,
      contact_status: 'found',
      contacts: [
        contact(2, 'editor@card.example.test'),
        contact(1, 'info@card.example.test', { bounced: true, last_contacted_at: WRITTEN }),
      ],
      letter_contact_id: 2,
      letter_note: 'прошлый адрес не дошёл — письмо уйдёт на следующий',
    });

    expect(within(rowOf('editor@card.example.test')).getByText('письмо уйдёт сюда')).toBeVisible();
    expect(within(rowOf('editor@card.example.test')).queryByText('не дошло')).toBeNull();
    expect(within(rowOf('info@card.example.test')).getByText('не дошло')).toBeVisible();
    expect(within(rowOf('info@card.example.test')).queryByText('письмо уйдёт сюда')).toBeNull();
    expect(
      within(addresses()).getByText('Прошлый адрес не дошёл — письмо уйдёт на следующий.'),
    ).toBeVisible();
  });

  it('адреса кончились — пометки на всех, отметки нет, причина словами сервера', async () => {
    await openCard({
      ...CARD,
      contact_status: 'found',
      contacts: [
        contact(1, 'info@card.example.test', { bounced: true }),
        contact(2, 'editor@card.example.test', { bounced: true }),
      ],
      letter_contact_id: null,
      letter_blocked: 'адреса кончились — все прежние не дошли',
    });

    expect(within(addresses()).getAllByText('не дошло')).toHaveLength(2);
    expect(screen.queryByText('письмо уйдёт сюда')).toBeNull();
    expect(within(addresses()).getByText('Адреса кончились — все прежние не дошли.')).toBeVisible();
  });

  it('первое письмо — без заметки: говорить не о чем', async () => {
    await openCard({
      ...CARD,
      contact_status: 'found',
      contacts: [contact(1, 'info@card.example.test'), contact(2, 'editor@card.example.test')],
      letter_contact_id: 1,
    });

    expect(within(rowOf('info@card.example.test')).getByText('письмо уйдёт сюда')).toBeVisible();
    expect(screen.queryByText('не дошло')).toBeNull();
    expect(screen.queryByText(/не дошёл/)).toBeNull();
  });
});
