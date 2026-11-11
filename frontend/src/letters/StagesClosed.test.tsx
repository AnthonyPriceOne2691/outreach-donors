/**
 * «Письма» и «Формы» у учётки без обоих этапов — по прямой ссылке (проверка прода 10.11.2026).
 *
 * Учётка только продаж открыла `/letters?stage=donors` и вместо одного «Раздел недоступен»
 * увидела три красные плашки: «Очередь не загрузилась», «Письма с неизвестным исходом не
 * загрузились», «Письма с отложенной доставкой не загрузились» — каждый запрос экрана получил свой
 * отказ сервера. То же — на `/letters?stage=advertisers`, `/forms` и `/forms?queue=advertisers`.
 * Пунктов меню на эти экраны у неё нет, но ссылку присылает коллега.
 *
 * Экран отвечает так же, как закрытый раздел (`auth/RequireAccess`): одно объяснение с названием
 * права той части, что в адресе, и ни одного запроса, на который сервер ответил бы 403 — запрос
 * на незаписанный адрес роняет тест сам (`test/server.ts`). Оба экрана — одним файлом: правило у
 * них одно, и проверяется оно одной таблицей адресов.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Me } from '../api/types';
import {
  ADVERTISERS_ONLY,
  DONORS_ONLY,
  NO_STUCK_LETTERS,
  OPERATOR,
  TOKEN_KEY,
} from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Recorded } from '../test/server';

/** Учётка только продаж: ни «Доноров», ни «Рекламодателей». */
const ONLY_SALES: Me = {
  ...OPERATOR,
  permissions: ['prices', 'run', 'sales', 'send', 'settings', 'usage', 'view'],
};

function open(path: string, who: Me, routes: Record<string, unknown> = {}): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, path);
  return recorded;
}

/** Запросы экрана: всё, кроме «кто я». */
const asked = (recorded: Recorded) =>
  recorded.calls.map((call) => call.path).filter((path) => path !== '/api/auth/me');

describe('без обоих этапов — один отказ раздела, а не отказы сервера', () => {
  it.each([
    ['/letters?stage=donors', 'Доноры'],
    ['/letters?stage=advertisers', 'Рекламодатели'],
    // Без части в адресе — первая часть экрана, как у учётки одного этапа.
    ['/letters', 'Доноры'],
    ['/forms', 'Доноры'],
    ['/forms?queue=advertisers', 'Рекламодатели'],
  ])('%s — «Раздел недоступен» с правом «%s»', async (path, right) => {
    const recorded = open(path, ONLY_SALES);

    expect(await screen.findByText('Раздел недоступен')).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(`^Действие «${right}» не выдано этой учётке\\.`)),
    ).toBeInTheDocument();
    // Объяснение одно: ни «не загрузилась», ни заголовка экрана, которого для учётки нет.
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.queryByText(/не загрузил/)).toBeNull();
    expect(screen.queryByRole('heading', { name: /Письма|Формы/ })).toBeNull();
    expect(asked(recorded)).toEqual([]);
  });
});

describe('учётке одного этапа экраны открыты, как были', () => {
  const LETTERS = {
    letters: [],
    queued_total: 0,
    batch_max: 200,
    followup_default: [7, 14],
    letter_default: { subject: 'Тема', zones: [] },
    blocked_by: [],
    transport: { name: 'null', real: false, problem: null },
    corridor: { min: 0.15, max: 0.25 },
    funnel: {},
  };
  const FORMS = {
    rows: [],
    total: 0,
    page: 1,
    limit: 20,
    monthly_left: 100,
    monthly_cap: 100,
    retry_days: 30,
  };

  it('«Аутрич»: адрес писем рекламодателям ведёт в письма донорам', async () => {
    const letters = open('/letters?stage=advertisers', DONORS_ONLY, {
      'GET /api/letters': { body: LETTERS },
      'GET /api/runs/with-accepted': { body: [] },
      ...NO_STUCK_LETTERS,
    });

    expect(await screen.findByRole('heading', { name: 'Письма донорам' })).toBeInTheDocument();
    expect(screen.queryByText('Раздел недоступен')).toBeNull();
    expect(asked(letters)).toContain('/api/letters');
  });

  it('«Рекламодатели»: адрес форм доноров ведёт в очередь рекламодателей', async () => {
    const forms = open('/forms', ADVERTISERS_ONLY, {
      'GET /api/advertisers/forms?page=1': { body: FORMS },
    });

    expect(await screen.findByRole('heading', { name: 'Формы' })).toBeInTheDocument();
    expect(screen.queryByText('Раздел недоступен')).toBeNull();
    expect(asked(forms)).toEqual(['/api/advertisers/forms?page=1']);
  });
});
