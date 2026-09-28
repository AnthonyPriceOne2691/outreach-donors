/**
 * Главная: число непривязанных ответов — строкой под плитками писем.
 *
 * Не плиткой в «Ждут человека»: убрать такой ответ оттуда пока нечем, и
 * янтарное число стояло бы вечно, с первого ответа на пробное письмо. Строка
 * появляется, когда такие ответы есть, и ведёт на вкладку, где их видно;
 * когда их нет, «всё в порядке» места на экране не занимает.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OVERVIEW, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function openOverview(unbound: number): Promise<HTMLElement> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/watchdog': { body: { alarms: [] } },
    'GET /api/overview': { body: { ...OVERVIEW, unbound_replies: unbound } },
  });
  renderWith(<AppRoutes />, '/');
  const title = await screen.findByText('Письма донорам');
  return title.closest('.mantine-Card-root') as HTMLElement;
}

describe('главная: непривязанные ответы', () => {
  it('есть — строка со счётом ведёт на вкладку «Не привязаны»', async () => {
    const letters = await openOverview(2);

    expect(within(letters).getByText(/Не привязаны ни к одному нашему письму/)).toBeVisible();
    expect(within(letters).getByRole('link', { name: '2 ответа' })).toHaveAttribute(
      'href',
      '/threads?tab=unbound',
    );
    // Плиток «Ждут человека» столько же, сколько было: строка — не плитка.
    const waiting = screen.getByText('Ждут человека').closest('.mantine-Card-root') as HTMLElement;
    expect(within(waiting).queryByText(/привязан/)).toBeNull();
  });

  it('слово при числе — по числу', async () => {
    const letters = await openOverview(5);

    expect(within(letters).getByRole('link', { name: '5 ответов' })).toBeInTheDocument();
  });

  it('нет — строки нет', async () => {
    const letters = await openOverview(0);

    expect(within(letters).queryByText(/Не привязаны/)).toBeNull();
  });
});
