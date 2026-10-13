/**
 * Сбой сервера на экране «Продаж» — словами (ревью стыков, круг 2).
 *
 * Отказ с текстом сервера раздел показывает целиком: очередь задач недоступна — 503 словами
 * обработчика `api/errors._queue_down`. Падение сервера (необработанное исключение: база не
 * отвечает, ошибка кода) и шлюз прокси при выкатке (502, 504) приходят без нашего текста —
 * и экран говорил «Сервер ответил 500»: код вместо того, что делать. Правка — в общем клиенте
 * (`api/client.ts`, `detailOf`): ответ 5xx без текста сервера — словами, код — в скобках
 * (решение владельца 13.10); пометки `it.fails` сняты.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };
const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
  gaps: [],
};

async function refusalOnOpen(answer: { status: number; raw?: string; body?: unknown }) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': answer,
    'GET /api/sales/leads': answer,
    'GET /api/sales/kb': { body: KB },
  });
  renderWith(<AppRoutes />, '/sales');
  const title = await screen.findByText('Раздел продаж не загрузился', {}, SCREEN_WAIT);
  const alert = title.closest('[role="alert"]');
  if (alert === null) throw new Error('отказ — не плашкой');
  return alert;
}

describe('продажи: сбой сервера — словами', () => {
  it('очередь задач недоступна — текст сервера целиком (опора теста ниже)', async () => {
    const said = 'Очередь задач недоступна — повторите позже';

    const alert = await refusalOnOpen({ status: 503, body: { detail: said } });

    expect(alert).toHaveTextContent(said);
  });

  it('сервер упал без текста — что делать словами, а не «Сервер ответил 500»', async () => {
    const alert = await refusalOnOpen({ status: 500, raw: 'Internal Server Error' });

    expect(alert).not.toHaveTextContent('Сервер ответил 500');
    expect(alert).toHaveTextContent('Сервер не справился с запросом (ответ 500) — повторите позже');
    expect(alert).toHaveTextContent('повторится — сообщите администратору');
  });

  it('шлюз прокси при выкатке (502) — тоже словами', async () => {
    const alert = await refusalOnOpen({ status: 502, raw: '<html>502 Bad Gateway</html>' });

    expect(alert).not.toHaveTextContent('Сервер ответил 502');
    expect(alert).toHaveTextContent('Сервер недоступен (ответ 502) — идёт выкатка');
    expect(alert).toHaveTextContent('повторите через минуту');
  });
});
