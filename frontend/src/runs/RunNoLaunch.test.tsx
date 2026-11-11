/**
 * «Прогон» у учётки без права запуска (проверка прода 10.11.2026).
 *
 * Экран и история прогонов такой учётке открыты, а список стран отвечал 403 — он требовал права
 * запуска, — и поле «Страна выдачи» стояло пустым: у выбранной страны не было подписи. Список —
 * справочник экрана, сервер отдаёт его всем, кому открыт экран (`api/runs/routes.py`). Запуск
 * закрыт, как был; того, в чём сервер откажет, экран не спрашивает.
 */

import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { countryTitle } from '../api/labels';
import type { Me } from '../api/types';
import { OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { history } from '../test/runFixtures';
import { serve } from '../test/server';

/** Оператор, у которого право запуска снято поимённо. */
const NO_LAUNCH: Me = {
  ...OPERATOR,
  permissions: OPERATOR.permissions.filter((right) => right !== 'run'),
};

describe('«Прогон» без права запуска', () => {
  it('страна выдачи — из списка сервера и с подписью, запуск закрыт', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: NO_LAUNCH },
      'GET /api/runs/countries': { body: ['us', 'de'] },
      'GET /api/runs?page=1': history([]),
    });
    renderWith(<AppRoutes />, '/run');

    // Без списка у выбора нет подписи — поле пустое; со списком страна названа словом.
    const country = await screen.findByRole('textbox', { name: 'Страна выдачи' });
    await waitFor(() => expect(country).toHaveValue(countryTitle('us')));
    expect(screen.getByText('Запускать прогоны не разрешено')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
    // Ключи, дававшие доноров, читаются правом запуска: их экран этой учётки не спрашивает —
    // запрос на незаписанный адрес уронил бы тест сам (`test/server.ts`).
    expect(recorded.calls.map((call) => call.path).sort()).toEqual([
      '/api/auth/me',
      '/api/runs/countries',
      '/api/runs?page=1',
    ]);
  });
});
