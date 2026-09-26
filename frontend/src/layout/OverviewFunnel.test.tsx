/**
 * Главная: воронка доноров без второго значения слова «донор».
 *
 * Решение 26.09.2026: донор — домен, принятый человеком. Записей в базе
 * больше — это проверенные домены, и главная называет их так. Плитка
 * «Доноры» ведёт в список доноров, «С адресом» — в тот же фильтр списка;
 * ссылка на один и тот же экран в одном разделе стоит одна.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OVERVIEW, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function openOverview(): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/watchdog': { body: { alarms: [] } },
    'GET /api/overview': { body: OVERVIEW },
  });
  renderWith(<AppRoutes />, '/');
  await screen.findByRole('heading', { name: 'Обзор' });
}

/** Плитка воронки по подписи — внутри раздела: «Доноры» есть и в меню. */
async function tile(title: string): Promise<HTMLElement> {
  const section = (await screen.findByText('Воронка доноров')).closest('.mantine-Card-root')!;
  return within(section as HTMLElement)
    .getByText(title)
    .closest('.metricTile, a, .mantine-Card-root') as HTMLElement;
}

describe('главная: воронка доноров', () => {
  it('проверенные домены — не доноры, донор — принятый человеком', async () => {
    await openOverview();

    // Разряды — неразрывным пробелом: «1 065».
    expect(await tile('Проверено доменов')).toHaveTextContent(/1\s065/);
    expect(OVERVIEW.donors.total).toBe(1065);
    const donors = await tile('Доноры');
    expect(donors.closest('a')).toHaveAttribute('href', '/donors');
    // «Принял человек» и «В базе» — прежние слова: одно называло донорами
    // не тех, другое спорило с плиткой.
    expect(screen.queryByText('Принял человек')).toBeNull();
    expect(screen.queryByText('В базе')).toBeNull();
  });

  it('«С адресом» — тем же фильтром списка доноров', async () => {
    await openOverview();

    expect((await tile('С адресом')).closest('a')).toHaveAttribute(
      'href',
      '/donors?has_contact=true',
    );
  });

  it('ссылка на список доноров в разделе одна — плитка', async () => {
    await openOverview();
    await screen.findByText('Воронка доноров');

    expect(screen.queryByRole('link', { name: 'Все доноры' })).toBeNull();
  });
});
