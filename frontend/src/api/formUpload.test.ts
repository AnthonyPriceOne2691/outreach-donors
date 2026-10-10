/**
 * Форма загрузки базы продаж уходит общим путём клиента (`upload`), а не своим `fetch`.
 *
 * До проверки прода 10.10.2026 у формы была своя копия разбора отказа: она не знала
 * признака отказа доступа — снятое право не перечитывало «кто я», — и обрыв связи
 * показывала текстом браузера. Общий путь — один разбор на все запросы.
 */

import { describe, expect, it, vi } from 'vitest';

import { watchAccessDenied } from '../auth/session';
import { TOKEN_KEY } from '../test/fixtures';
import { serve } from '../test/server';
import { DeniedError } from './client';
import { previewImport } from './sales';

const SOURCE = { file: null, link: 'https://docs.example.test/sheet' };

describe('форма загрузки базы продаж', () => {
  it('отказ доступа с признаком зовёт сессию перечитать «кто я», как любой запрос', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const denied = vi.fn();
    const stop = watchAccessDenied(denied);
    const recorded = serve({
      'POST /api/sales/import/preview': {
        status: 403,
        body: { detail: 'Действие «Продажи» не выдано этой учётке — права выдаёт админ' },
        headers: { 'X-Access-Refusal': 'permission' },
      },
    });

    await expect(previewImport(SOURCE, {})).rejects.toBeInstanceOf(DeniedError);
    expect(denied).toHaveBeenCalledTimes(1);
    expect(recorded.calls[0]?.token).toBe('Bearer пропуск');
    stop();
  });
});
