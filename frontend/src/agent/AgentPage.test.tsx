/**
 * Экран агента переписки: у этапов свои настройки, правка заводит версию,
 * не настроенный этап говорит, что агент на нём не пишет.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AgentSettingsBody, AgentStageView, AgentView } from '../api/agent';
import type { LetterStage } from '../api/types';
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

/** Как этапы приходят из реестра сервера: имя, пояснение и сторона цены. */
const ABOUT: Record<LetterStage, Pick<AgentStageView, 'title' | 'lead' | 'price_side'>> = {
  donors: { title: 'Донорам', lead: 'Донорам мы покупаем размещение.', price_side: 'buy' },
  advertisers: {
    title: 'Рекламодателям',
    lead: 'Рекламодателям мы продаём размещение.',
    price_side: 'sell',
  },
};

function stage(name: LetterStage, defaults: AgentSettingsBody): AgentStageView {
  const autopilot = { autopilot_allowed: false, autopilot_refusal: null };
  return { stage: name, ...ABOUT[name], current: null, defaults, history: [], ...autopilot };
}

const BLANK: AgentView = { stages: [stage('donors', DONORS), stage('advertisers', ADVERTISERS)] };

const VERSION = {
  version: 2,
  created_by: 'ivan@site.com',
  created_at: '2026-10-04T10:00:00+00:00',
  settings: { ...DONORS, price_limit_usd: '150.00' },
};

const OFF_WORDS =
  'Автопилот на этом сервере выключен (OUTREACH_AGENT_AUTOPILOT) — агент пишет только черновики';

/** Доноры в автопилоте: выключатель снят (`allowed` — нет) или включён. */
function onAutopilot(allowed: boolean): AgentView {
  const version = { ...VERSION, settings: { ...VERSION.settings, mode: 'autopilot' as const } };
  const donors = { ...stage('donors', DONORS), current: version, history: [version] };
  const words = allowed ? null : OFF_WORDS;
  return {
    stages: [
      { ...donors, autopilot_allowed: allowed, autopilot_refusal: words },
      stage('advertisers', ADVERTISERS),
    ],
  };
}

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
  await screen.findByLabelText('Цель разговора');
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
    // Что этап не настроен, сказано строкой наверху — пустой карточки версий нет.
    expect(screen.queryByRole('button', { name: /^Версии/ })).not.toBeInTheDocument();
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

  it('предел цены с дробью: «99,5» и «12.5» остаются в поле и уходят с центами', async () => {
    // Проверка QA 10.10.2026: поле стиралось на точке — «12.5» давало 5, «99,5» — тоже 5.
    const recorded = await openAgent(BLANK, {
      'POST /api/agent/settings/donors': { body: { ...VERSION, version: 1 } },
    });
    const user = userEvent.setup();
    const price = screen.getByLabelText('Не дороже, $');

    await user.type(price, '99,5');
    expect(price).toHaveValue('99,5');
    await user.clear(price);
    await user.type(price, '12.5');
    expect(price).toHaveValue('12.5');
    await user.click(screen.getByRole('button', { name: /Сохранить новой версией/ }));

    await waitFor(() => {
      const saved = recorded.calls.find((call) => call.method === 'POST');
      expect(saved?.body).toMatchObject({ price_limit_usd: '12.50' });
    });
  });

  it('буква в цене — отказ под полем, а не склеенные цифры', async () => {
    await openAgent();
    const user = userEvent.setup();

    // «ю» — клавиша точки на русской раскладке: числовое поле выбрасывало её, и «12ю5» было 125.
    await user.type(screen.getByLabelText('Не дороже, $'), '12ю5');

    expect(screen.getByLabelText('Не дороже, $')).toHaveValue('12ю5');
    expect(screen.getByText('Только число, например 99,50')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
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

  it('этап из реестра сервера виден на экране — без правки экрана', async () => {
    const newcomer: AgentStageView = {
      stage: 'sales',
      title: 'Лидам',
      lead: 'Лидам мы отвечаем фактами из базы знаний.',
      price_side: 'sell',
      current: null,
      defaults: { ...DONORS, goal: 'Довести разговор до созвона' },
      history: [],
      autopilot_allowed: false,
      autopilot_refusal: null,
    };
    const recorded = await openAgent(
      { stages: [...BLANK.stages, newcomer] },
      {
        'POST /api/agent/settings/sales': { body: { ...VERSION, version: 1 } },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByText('Лидам'));

    expect(screen.getByText('Лидам мы отвечаем фактами из базы знаний.')).toBeInTheDocument();
    expect(screen.getByLabelText('Цель разговора')).toHaveValue('Довести разговор до созвона');
    expect(screen.getByLabelText('Не дешевле, $')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Сохранить новой версией/ }));
    await waitFor(() => {
      expect(recorded.calls.some((call) => call.path === '/api/agent/settings/sales')).toBe(true);
    });
  });

  it.each([
    ['без права «Продажи» этапа продаж нет — даже присланного сервером', ['settings', 'view']],
    ['с правом «Продажи» этап продаж на месте', ['sales', 'settings', 'view']],
  ])('%s', async (_, permissions) => {
    // Решение Anthony 10.10.2026 (П2): сервер без права этап продаж не отдаёт, а экран
    // не показывает его и сам — ответ в кэше мог прийти до того, как право сняли.
    const sales: AgentStageView = {
      ...stage('advertisers', ADVERTISERS),
      stage: 'sales',
      title: 'Лидам',
      lead: 'Лидам мы отвечаем фактами из базы знаний.',
    };
    await openAgent({ stages: [...BLANK.stages, sales] }, {}, { ...OPERATOR, permissions });

    expect(screen.getByText('Рекламодателям')).toBeInTheDocument();
    expect(screen.queryByText('Лидам') !== null).toBe(permissions.includes('sales'));
  });

  it('автопилот выбран, а выключатель снят — словами, что письма сами не уходят', async () => {
    await openAgent(onAutopilot(false));

    expect(screen.getByText('Автопилот выбран, но письма сами не уходят')).toBeInTheDocument();
    expect(screen.getByText(OFF_WORDS)).toBeInTheDocument();
  });

  it('выключатель включён — предупреждения нет', async () => {
    await openAgent(onAutopilot(true));

    expect(screen.getByText(/Действует версия 2/)).toBeInTheDocument();
    expect(
      screen.queryByText('Автопилот выбран, но письма сами не уходят'),
    ).not.toBeInTheDocument();
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
    // Прежние версии — по нажатию: действующая названа строкой наверху.
    const versions = screen.getByRole('button', { name: /^Версии · / });
    expect(versions).toHaveAttribute('aria-expanded', 'false');
    await userEvent.setup().click(versions);
    expect(versions).toHaveAttribute('aria-expanded', 'true');
    // Раскрытие идёт кадром анимации: ждём, пока станет видно.
    await waitFor(() => expect(screen.getByText('№2')).toBeVisible());
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

describe('агент переписки: набранное не пропадает молча (проверка прода 10.10.2026)', () => {
  /** Куда ведёт пункт меню в этих тестах: стоп-лист, пустой. */
  const ELSEWHERE = {
    'GET /api/suppressions': { body: { rows: [], total: 0, donor_decisions: 0 } },
  };

  function menuLink(name: string) {
    return within(screen.getByRole('navigation', { name: 'Разделы' })).getByRole('link', { name });
  }

  it('уход по меню с несохранённым — вопрос, «Остаться» оставляет набранное', async () => {
    await openAgent(BLANK, ELSEWHERE);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Тон'), ', по делу');
    await user.click(menuLink('Стоп-лист'));

    const dialog = await screen.findByRole('dialog', { name: 'Уйти без сохранения?' });
    expect(within(dialog).getByText(/настройках агента: «Донорам»/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Остаться' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Тон')).toHaveValue('Вежливо и коротко, по делу');
  });

  it('«Уйти без сохранения» ведёт туда, куда вела ссылка', async () => {
    await openAgent(BLANK, ELSEWHERE);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Тон'), ', по делу');
    await user.click(menuLink('Стоп-лист'));
    const dialog = await screen.findByRole('dialog', { name: 'Уйти без сохранения?' });
    await user.click(within(dialog).getByRole('button', { name: 'Уйти без сохранения' }));

    expect(await screen.findByText(/Список пуст/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Тон')).not.toBeInTheDocument();
  });

  it('набранное на другом этапе тоже держит, и этап назван', async () => {
    await openAgent(BLANK, ELSEWHERE);
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));
    await user.type(screen.getByLabelText('Тон'), ', по делу');
    await user.click(screen.getByRole('radio', { name: 'Донорам' }));
    await user.click(menuLink('Стоп-лист'));

    const dialog = await screen.findByRole('dialog', { name: 'Уйти без сохранения?' });
    expect(within(dialog).getByText(/«Рекламодателям»/)).toBeInTheDocument();
  });

  it('без правки — и правка, вернувшая действующие, — уход без вопроса', async () => {
    await openAgent(BLANK, ELSEWHERE);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Тон'), '   ');
    await user.click(menuLink('Стоп-лист'));

    expect(await screen.findByText(/Список пуст/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('закрытие и обновление вкладки с несохранённым — браузер спрашивает', async () => {
    await openAgent();
    const user = userEvent.setup();
    const leave = () => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };

    expect(leave()).toBe(false);
    await user.type(screen.getByLabelText('Тон'), ', по делу');
    expect(leave()).toBe(true);
  });
});
