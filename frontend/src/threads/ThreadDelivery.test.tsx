/**
 * Доставка словами в переписке (29.10.2026): под нашим письмом — что почта сказала о доставке.
 * Письмо, которое сервер получателя не принимает, больше не стоит одной галочкой «принято
 * платформой»: строка называет причину, попытки и часы; отказ — словами причины.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { LetterDeferral, LetterDelivery } from '../api/delivery';
import type { LetterCard } from '../api/types';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { letter, threadWith } from '../test/threadFixtures';

const DEFERRAL: LetterDeferral = {
  kind: 'unreachable',
  words: 'сервер получателя не отвечает (порт 25, тайм-аут)',
  attempts: 14,
  since: new Date(2026, 9, 8, 10, 26).toISOString(),
  last: new Date(2026, 9, 8, 16, 22).toISOString(),
};

async function openThread(letters: (LetterCard & LetterDelivery)[]) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/8': { body: threadWith({ letters, incoming: [] }) },
  });
  renderWith(<AppRoutes />, '/threads/8');
  await screen.findByRole('heading', { name: 'prices.example.test' });
}

function noteOf(tone: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-delivery="${tone}"]`);
}

describe('доставка под письмом', () => {
  it('ушло и откладывается — причина, попытки и часы', async () => {
    await openThread([{ ...letter({ status: 'sent' }), deferral: DEFERRAL }]);

    const note = noteOf('waiting');
    expect(note).not.toBeNull();
    expect(note?.textContent).toMatch(
      /^Доставка откладывается: сервер получателя не отвечает \(порт 25, тайм-аут\) · 14 попыток с 08\.10, 10:26, последняя 08\.10, 16:22$/,
    );
    expect(screen.getByLabelText('принято платформой')).toBeInTheDocument();
  });

  it('не доставлено — словами причины, и значок остаётся', async () => {
    await openThread([
      {
        ...letter({ status: 'bounced' }),
        refusal: 'ящика получателя нет или он отключён (5.1.1)',
        deferral: null,
      },
    ]);

    expect(noteOf('refused')?.textContent).toBe(
      'Не доставлено: ящика получателя нет или он отключён (5.1.1)',
    );
    expect(screen.getByText('отказ доставки')).toBeInTheDocument();
  });

  it('доставлено сразу — строки нет', async () => {
    await openThread([letter({ status: 'delivered' })]);

    expect(document.querySelector('[data-delivery]')).toBeNull();
  });
});
