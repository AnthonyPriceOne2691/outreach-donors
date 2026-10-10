/**
 * Модуль продаж выключен — строка под шапкой раздела на любой вкладке и в мастере загрузки
 * (находка QA соседей на проде: выключенный модуль выглядел рабочим). Включён — строки нет.
 * Включён ли модуль, говорит сервер в списке гипотез (`module_enabled`). Ответы сервера —
 * записанные (`serve()`), незаписанный запрос роняет тест.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { ChainView, SalesFunnelView, SenderView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };
const LINE = 'Модуль продаж выключен — письма не уходят, разбор и передача не идут';
const ALL_MISSING = ['первого письма', 'первой добивки', 'второй добивки'];

const CHAIN: ChainView = {
  hypothesis_id: null,
  rows: [],
  chains: ['ru', 'en'].map((language) => ({
    language,
    source: 'common' as const,
    missing: ALL_MISSING,
    version: `chain-${language}0000000000`,
  })),
  steps: [1, 2, 3],
  languages: ['ru', 'en'],
  placeholders: ['name', 'company', 'site'],
  limits: { subject: 255, body: 10000 },
};

const NONE = { queued: 0, sent: 0, delivered: 0, bounced: 0, answered: 0, handed_off: 0 };
const FUNNEL: SalesFunnelView = {
  hypothesis_id: null,
  since: null,
  until: null,
  rows: [],
  total: NONE,
};

const SENDER: SenderView = {
  sender_name: null,
  sender_position: null,
  signature: null,
  website: null,
  telegram: null,
  physical_address: null,
  call_link: null,
  updated_by: null,
  updated_at: null,
  missing: ['не задана подпись'],
  limits: {
    sender_name: 128,
    sender_position: 128,
    signature: 1000,
    website: 255,
    telegram: 255,
    physical_address: 500,
    call_link: 512,
  },
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const LEADS = {
  rows: [],
  total: 0,
  page: 1,
  limit: 20,
  states: { new: 0, ready: 0, rejected: 0 },
  reasons: {},
};

/** Экран и текст, по которому видно, что он отрисован. */
const SCREENS: [string, string, string | RegExp][] = [
  ['лиды', '/sales', 'Лидов пока нет.'],
  ['гипотезы', '/sales?tab=hypotheses', 'Гипотез пока нет.'],
  ['цепочка писем', '/sales?tab=chain', /Шаблонов в наборе нет/],
  ['очередь писем', '/sales?tab=queue', /Очередь собирается из лидов/],
  ['воронка', '/sales?tab=funnel', /Воронка считается по лидам гипотез/],
  ['база знаний', '/sales?tab=kb', 'Записей пока нет — агенту не из чего писать.'],
  ['отправитель', '/sales?tab=sender', /Отправка продаж/],
  ['мастер загрузки', '/sales/import', 'Гипотез пока нет'],
];

async function open(path: string, ready: string | RegExp, hypotheses: unknown): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/leads': { body: LEADS },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/chain': { body: CHAIN },
    'GET /api/sales/funnel': { body: FUNNEL },
    'GET /api/sales/sender': { body: SENDER },
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready, {}, SCREEN_WAIT);
}

function lines(): Element[] {
  return [...document.querySelectorAll('.salesModuleOff')];
}

describe('модуль продаж выключен — строка под шапкой раздела', () => {
  it.each(SCREENS)('%s: выключен — одна строка, словами, без имени настройки', async (...args) => {
    const [, path, ready] = args;
    await open(path, ready, { rows: [], total: 0, module_enabled: false });

    expect(lines()).toHaveLength(1);
    expect(lines()[0]).toHaveTextContent(LINE);
    expect(document.body.textContent).not.toMatch(/SALES_ENABLED/);
  });

  it.each(SCREENS)('%s: включён — строки нет', async (...args) => {
    const [, path, ready] = args;
    await open(path, ready, { rows: [], total: 0, module_enabled: true });

    expect(lines()).toEqual([]);
    expect(screen.queryByText(/Модуль продаж выключен/)).not.toBeInTheDocument();
  });

  it('строка стоит над вкладками — под шапкой раздела, а не внутри вкладки', async () => {
    await open('/sales?tab=kb', 'Записей пока нет — агенту не из чего писать.', {
      rows: [],
      total: 0,
      module_enabled: false,
    });

    const line = lines()[0];
    const head = screen.getByRole('heading', { name: 'Продажи' });
    const tabs = screen.getByRole('radiogroup', { name: 'Вкладки продаж' });
    if (line === undefined) throw new Error('строки нет');
    expect(head.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(line.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('ответ без поля — старый сервер посреди выкладки — строкой не пугает', async () => {
    await open('/sales', 'Лидов пока нет.', { rows: [], total: 0 });

    expect(lines()).toEqual([]);
  });
});
