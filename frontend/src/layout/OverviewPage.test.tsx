/**
 * Обзор и сторож тишины.
 *
 * Сторож стоит на главной, потому что поломка этого класса не
 * показывает себя нигде: человек увидит, что «всё тихо», и закроет
 * вкладку. Поэтому проверяется и то, что тревога видна, и то, что
 * в тишине по делу экран не пугает.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function openOverview(alarms: unknown[]) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/watchdog': { body: { alarms } },
  });
  renderWith(<AppRoutes />, '/');
  // «Обзор» есть и в меню, и в заголовке карточки — ждём заголовок.
  await screen.findByRole('heading', { name: 'Обзор' });
}

describe('сторож тишины на обзоре', () => {
  it('тревога видна целиком, вместе с тем, что делать', async () => {
    await openOverview([
      {
        code: 'delivery-silence',
        title: 'Платформа молчит о доставке',
        detail: '12 писем отправлены больше 6 часов назад, и ни по одному не пришло события.',
      },
    ]);

    expect(await screen.findByText('Платформа молчит о доставке')).toBeInTheDocument();
    expect(screen.getByText(/ни по одному не пришло события/)).toBeInTheDocument();
  });

  it('в тишине по делу экран не пугает', async () => {
    await openOverview([]);

    expect(await screen.findByText(/Тихо и правильно/)).toBeInTheDocument();
  });
});
