/**
 * В экранах продаж нет команд консоли (решение владельца 10.10.2026 по QA соседней сессии:
 * «формы в интерфейсе»). Гипотезу заводит окно, очистку запускает кнопка, цепочку и базу
 * знаний задают здесь, а загрузка набора файлом — дело администратора, без команды на экране.
 *
 * Отрисовывается каждая вкладка раздела и мастер в пустом состоянии — команды жили именно
 * в пустых состояниях и подсказках; текст страницы целиком не содержит `outreach `.
 * Комментарии в коде называть команды могут: их на экране нет.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { ChainView, SalesFunnelView, SenderView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };
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

describe('экраны продаж без команд консоли', () => {
  it.each([
    ['лиды', '/sales', 'Лидов пока нет.'],
    ['гипотезы', '/sales?tab=hypotheses', 'Гипотез пока нет.'],
    ['цепочка писем', '/sales?tab=chain', /Шаблонов в наборе нет/],
    ['очередь писем', '/sales?tab=queue', /Очередь собирается из лидов гипотезы/],
    ['воронка', '/sales?tab=funnel', /Воронка считается по лидам гипотез/],
    ['база знаний', '/sales?tab=kb', 'Записей пока нет — агенту не из чего писать.'],
    ['отправитель', '/sales?tab=sender', /Отправка продаж/],
    ['мастер загрузки', '/sales/import', 'Гипотез пока нет'],
  ])('%s: на экране нет «outreach …»', async (_name, path, ready) => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } },
      'GET /api/sales/leads': { body: LEADS },
      'GET /api/sales/kb': { body: KB },
      'GET /api/sales/chain': { body: CHAIN },
      'GET /api/sales/funnel': { body: FUNNEL },
      'GET /api/sales/sender': { body: SENDER },
    });
    renderWith(<AppRoutes />, path);

    await screen.findByText(ready, {}, SCREEN_WAIT);

    expect(document.body.textContent).not.toMatch(/outreach /);
  });
});
