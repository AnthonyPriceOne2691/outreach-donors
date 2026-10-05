/**
 * Экран агента переписки: у этапов свои настройки, правка заводит версию,
 * не настроенный этап говорит, что агент на нём не пишет.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AgentSettingsBody, AgentStageView, AgentView } from '../api/agent';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const DONORS: AgentSettingsBody = {
  enabled: true,
  goal: 'Узнать цену размещения',
  tone: 'Вежливо и коротко',
  points: ['Спросить цену', 'Попросить скидку'],
  price_limit_usd: null,
  stop_topics: ['Договор'],
};

const ADVERTISERS: AgentSettingsBody = {
  ...DONORS,
  goal: 'Продать размещение',
  points: ['Назвать цену'],
};

function stage(name: AgentStageView['stage'], defaults: AgentSettingsBody): AgentStageView {
  return { stage: name, current: null, defaults, history: [] };
}

const BLANK: AgentView = { stages: [stage('donors', DONORS), stage('advertisers', ADVERTISERS)] };

const VERSION = {
  version: 2,
  created_by: 'ivan@site.com',
  created_at: '2026-10-04T10:00:00+00:00',
  settings: { ...DONORS, price_limit_usd: '150.00' },
};

const CONFIGURED: AgentView = {
  stages: [
    { ...stage('donors', DONORS), current: VERSION, history: [VERSION] },
    stage('advertisers', ADVERTISERS),
  ],
};

async function openAgent(
  view: AgentView = BLANK,
  routes: Record<string, unknown> = {},
  user: unknown = ADMIN,
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: user },
    'GET /api/agent/settings': { body: view },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/agent');
  // Заголовок стоит и до ответа сервера — ждём того, что рисуется по ответу.
  await screen.findByRole('heading', { name: 'Версии' });
  return recorded;
}

describe('агент переписки', () => {
  it('не настроенный этап: агент не пишет, умолчания — отправная точка', async () => {
    await openAgent();

    expect(screen.getByText(/Этап не настроен — агент на нём не пишет/)).toBeInTheDocument();
    expect(screen.getByLabelText('Цель разговора')).toHaveValue('Узнать цену размещения');
    expect(screen.getByLabelText('Доводы и вопросы')).toHaveValue(
      'Спросить цену\nПопросить скидку',
    );
    expect(screen.getByText(/Версий ещё нет/)).toBeInTheDocument();
    // Умолчания сохраняют и без правки: с них агент начинает писать.
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeEnabled();
  });

  it('сохраняет новую версию этапа строками без пустых', async () => {
    const recorded = await openAgent(BLANK, {
      'POST /api/agent/settings/donors': { body: { ...VERSION, version: 1 } },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Доводы и вопросы'), '\n\n  Уточнить срок  ');
    await user.type(screen.getByLabelText('Не дороже, $'), '150');
    await user.click(screen.getByRole('button', { name: /Сохранить новой версией/ }));

    await waitFor(() => {
      expect(screen.getByText(/сохранены как версия 1/)).toBeInTheDocument();
    });
    const saved = recorded.calls.find((call) => call.method === 'POST');
    expect(saved?.body).toEqual({
      ...DONORS,
      points: ['Спросить цену', 'Попросить скидку', 'Уточнить срок'],
      price_limit_usd: '150.00',
    });
  });

  it('у этапов свои настройки, и набранное не теряется при переключении', async () => {
    await openAgent();
    const user = userEvent.setup();
    const goal = screen.getByLabelText('Цель разговора');
    await user.clear(goal);
    await user.type(goal, 'Своя цель');

    await user.click(screen.getByText('Рекламодателям'));
    expect(screen.getByLabelText('Цель разговора')).toHaveValue('Продать размещение');
    expect(screen.getByLabelText('Не дешевле, $')).toBeInTheDocument();

    await user.click(screen.getByText('Донорам'));
    expect(screen.getByLabelText('Цель разговора')).toHaveValue('Своя цель');
  });

  it('действующая версия: кто и когда, сохранять нечего до правки, правку можно вернуть', async () => {
    await openAgent(CONFIGURED);
    const user = userEvent.setup();

    expect(screen.getByText(/Действует версия 2 — ivan@site.com/)).toBeInTheDocument();
    expect(screen.getByText('пишет черновики')).toBeInTheDocument();
    const save = screen.getByRole('button', { name: /Сохранить новой версией/ });
    expect(save).toBeDisabled();

    await user.click(screen.getByLabelText('Агент готовит черновики ответов на этом этапе'));
    expect(save).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Вернуть действующие' }));
    expect(save).toBeDisabled();
  });

  it('отказ — под полем и до нажатия', async () => {
    await openAgent();
    const user = userEvent.setup();

    await user.clear(screen.getByLabelText('Цель разговора'));

    expect(screen.getByText('Пусто — напишите хотя бы фразу')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
  });

  it('без права на настройки правка закрыта, а действующие видны', async () => {
    await openAgent(CONFIGURED, {}, { ...OPERATOR, permissions: ['view'] });

    expect(screen.getByText('Править настройки агента не разрешено')).toBeInTheDocument();
    expect(screen.getByLabelText('Цель разговора')).toBeDisabled();
    expect(screen.getByText('№2')).toBeInTheDocument();
  });

  it('отказ сервера на чтении — причина словами', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/agent/settings': { status: 503, body: { detail: 'База недоступна' } },
    });
    renderWith(<AppRoutes />, '/agent');

    expect(await screen.findByText('Настройки агента не загрузились')).toBeInTheDocument();
    expect(screen.getByText('База недоступна')).toBeInTheDocument();
  });
});
