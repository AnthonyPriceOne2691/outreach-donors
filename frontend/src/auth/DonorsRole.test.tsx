/**
 * Роль «Только доноры» на экранах: Этап 1 — целиком, Этапа 2 и продаж нет.
 *
 * Сервер закрывает маршруты сам (`access/permissions.py`), а экран не предлагает того, в чём он
 * откажет: без права нет группы меню, плиток, вкладок и строк Этапа 2, и нет запросов, на которые
 * пришёл бы отказ. Иначе человек видел бы красные ошибки там, где просто работает с донорами.
 * Запрос на незаписанный адрес роняет тест сам (`test/server.ts`); запрос Этапа 2 в записанных
 * ответах ловит `closedCalls`.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ROLE_TITLES } from '../api/labels';
import type { OverviewView } from '../api/types';
import { ADMIN, card, DONORS_ONLY, OPERATOR, OVERVIEW, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Recorded } from '../test/server';
import { openUsage, SPENDING } from '../test/usageFixtures';

/** Сводка так, как её отдаёт сервер без права «Рекламодатели»: этапа в письмах нет вовсе. */
const ONLY_DONORS_OVERVIEW: OverviewView = {
  ...OVERVIEW,
  letters: { donors: OVERVIEW.letters.donors },
};

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

const QUEUE_LINE = { stage: 'donors', letters: [], after_minutes: 5, total: 0 };

/** Адреса Этапа 2: их экран роли не спрашивает, даже если ответ записан. */
const CLOSED = /^\/api\/(advertisers|crawls|donors\/import|settings\/advertisers|replies\/leads)/;

function closedCalls({ calls }: Recorded): string[] {
  return calls
    .map((call) => call.path)
    .filter((path) => CLOSED.test(path) || path.includes('stage=advertisers'));
}

function open(path: string, routes: Record<string, unknown> = {}): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: DONORS_ONLY },
    'GET /api/watchdog': { body: { alarms: [] } },
    'GET /api/overview': { body: ONLY_DONORS_OVERVIEW },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, path);
  return recorded;
}

const menu = async () => within(await screen.findByRole('navigation', { name: 'Разделы' }));

describe('меню и «Обзор» роли «Только доноры»', () => {
  it('в меню — доноры и настройки, групп рекламодателей и продаж нет', async () => {
    const recorded = open('/');

    const items = await menu();
    await items.findByRole('group', { name: 'Доноры' });
    expect(items.getByRole('group', { name: 'Настройки' })).toBeInTheDocument();
    expect(items.queryByRole('group', { name: 'Рекламодатели' })).not.toBeInTheDocument();
    expect(items.queryByRole('group', { name: 'Продажи' })).not.toBeInTheDocument();
    expect(items.getByRole('link', { name: 'Письма донорам' })).toBeInTheDocument();
    expect(items.getByRole('link', { name: 'Диалоги с донорами' })).toBeInTheDocument();
    expect(items.queryByRole('link', { name: /рекламодател/i })).not.toBeInTheDocument();
    expect(items.queryByRole('link', { name: /продаж/i })).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('«Обзор» без Этапа 2: сводка на месте, плиток и строки рекламодателей нет, ошибки нет', async () => {
    const recorded = open('/');

    expect(await screen.findByText('Заполнить формы')).toBeInTheDocument();
    expect(screen.getByText('Воронка доноров')).toBeInTheDocument();
    expect(screen.getByText('Письма донорам')).toBeInTheDocument();
    expect(screen.queryByText('Лиды рекламодателей')).not.toBeInTheDocument();
    expect(screen.queryByText('Кандидаты в рекламодатели')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Рекламодателям/)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('адрес раздела объясняет, какого права нет, а не ломится на сервер', async () => {
    const recorded = open('/advertisers');

    expect(await screen.findByText('Раздел недоступен')).toBeInTheDocument();
    expect(screen.getByText(/Действие «Рекламодатели» не выдано/)).toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });
});

describe('оператор и админ ничего не потеряли', () => {
  it.each([
    ['оператор', OPERATOR],
    ['админ', ADMIN],
  ])('%s видит группу рекламодателей и плитки Этапа 2', async (_, who) => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: who },
      'GET /api/watchdog': { body: { alarms: [] } },
      'GET /api/overview': { body: OVERVIEW },
    });
    renderWith(<AppRoutes />, '/');

    expect(await screen.findByText('Лиды рекламодателей')).toBeInTheDocument();
    expect(screen.getByText('Кандидаты в рекламодатели')).toBeInTheDocument();
    expect(screen.getByText('Рекламодателям писем ещё не было.')).toBeInTheDocument();
    expect((await menu()).getByRole('group', { name: 'Рекламодатели' })).toBeInTheDocument();
  });
});

describe('рабочие экраны роли: адрес Этапа 2 ведёт к донорам', () => {
  it.each(['/letters', '/letters?stage=advertisers'])(
    '%s — «Письма донорам» без переключателя адресата',
    async (path) => {
      const recorded = open(path, {
        'GET /api/letters': { body: { ...LETTERS, stage: 'donors', audience: 'links' } },
        'GET /api/letters/unknown': { body: QUEUE_LINE },
        'GET /api/letters/deferred': { body: QUEUE_LINE },
        'GET /api/runs/with-accepted': { body: [] },
      });

      expect(await screen.findByRole('heading', { name: 'Письма донорам' })).toBeInTheDocument();
      expect(screen.queryByRole('radiogroup', { name: 'Кому письма' })).not.toBeInTheDocument();
      expect(screen.queryByText('Очередь не загрузилась')).not.toBeInTheDocument();
      expect(closedCalls(recorded)).toEqual([]);
    },
  );

  it('формы: своя очередь без вкладок, адрес очереди рекламодателей её не открывает', async () => {
    const recorded = open('/forms?queue=advertisers', {
      'GET /api/contacts/forms?page=1': {
        body: { rows: [], total: 0, page: 1, limit: 20, monthly_left: 9, monthly_cap: 10 },
      },
    });

    expect(await screen.findByText(/Очередь пуста/)).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Чья очередь форм' })).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('диалоги: часть доноров, без «Кому писали» и без выгрузки лидов', async () => {
    const recorded = open('/threads?stage=advertisers&state=lead', {
      'GET /api/threads': { body: [] },
      'GET /api/replies/calibration': { body: { versions: [] } },
      'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    });

    expect(await screen.findByRole('heading', { name: 'Диалоги с донорами' })).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Кому писали' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Выгрузить лиды' })).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('пороги: карточки весов Этапа 2 нет, и её запроса тоже', async () => {
    const recorded = open('/settings', {
      'GET /api/settings/thresholds': {
        body: {
          current: null,
          defaults: {
            min_dr: 20,
            min_org_traffic: 1000,
            min_refdomains: 50,
            min_keywords: 100,
            max_dr: 80,
            geo_top_n: 5,
            geo_min_share_percent: 10,
            metrics_ttl_days: 90,
          },
          history: [],
          limits: {
            min_dr: { min: 0, max: 90 },
            min_org_traffic: { min: 0, max: 10_000_000 },
            min_refdomains: { min: 0, max: 1_000_000 },
            min_keywords: { min: 0, max: 1_000_000 },
            max_dr: { min: 1, max: 100 },
          },
          rule_limits: {
            geo_top_n: { min: 1, max: 5 },
            geo_min_share_percent: { min: 1, max: 100 },
            metrics_ttl_days: { min: 1, max: 365 },
          },
        },
      },
    });

    expect(await screen.findByRole('heading', { name: 'Пороги отбора' })).toBeInTheDocument();
    expect(screen.queryByText(/Этапа 2/)).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('расход: потолка обхода нет, а что счёт не весь — сказано словами', async () => {
    await openUsage(
      { ...SPENDING, sales_hidden: true, advertisers_hidden: true },
      { me: DONORS_ONLY },
    );

    expect(screen.queryByText(/прокси и рендеринг/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Расход продаж и рекламодателей не показан — его видно с правами/),
    ).toBeInTheDocument();
  });

  it('расход без одних продаж говорит прежними словами и показывает потолок обхода', async () => {
    await openUsage({ ...SPENDING, sales_hidden: true, advertisers_hidden: false });

    expect(screen.getByText(/прокси и рендеринг с начала месяца/)).toBeInTheDocument();
    expect(
      screen.getByText(/Расход продаж не показан — его видно с правом «Продажи»/),
    ).toBeInTheDocument();
  });
});

describe('роль на экране учёток', () => {
  async function openUsers(routes: Record<string, unknown> = {}) {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/users': { body: [card(ADMIN), card(OPERATOR), card(DONORS_ONLY)] },
      ...(routes as Record<string, never>),
    });
    renderWith(<AppRoutes />, '/users');
    await screen.findByText(DONORS_ONLY.email);
    return recorded;
  }

  it('роль учётки названа словами и меняется в строке', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/2': { body: card({ ...OPERATOR, role: 'donors' }) },
    });
    const user = userEvent.setup();

    const role = (who: string) => screen.getByRole('textbox', { name: `Роль ${who}` });
    expect(role(DONORS_ONLY.email)).toHaveValue(ROLE_TITLES.donors);
    await user.click(role(OPERATOR.email));
    await user.click(await screen.findByRole('option', { name: ROLE_TITLES.donors }));

    const patched = recorded.calls.find((call) => call.method === 'PATCH');
    expect(patched?.body).toEqual({ role: 'donors' });
  });

  it('новую учётку заводят этой ролью', async () => {
    const issued = {
      user: card({ ...DONORS_ONLY, id: 9 }, { must_change_password: true }),
      password: 'SrSoH3WhmVXhaGxw', // pragma: allowlist secret
      note: 'Пароль показан один раз.',
    };
    const recorded = await openUsers({ 'POST /api/users': { status: 201, body: issued } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Завести учётку' }));
    await user.type(await screen.findByLabelText('Почта'), 'новый@site.com');
    await user.click(screen.getByRole('textbox', { name: 'Роль' }));
    await user.click(await screen.findByRole('option', { name: /^Только доноры/ }));
    await user.click(screen.getByRole('button', { name: 'Завести' }));

    await screen.findByText('SrSoH3WhmVXhaGxw');
    const posted = recorded.calls.find((call) => call.method === 'POST');
    expect(posted?.body).toEqual({ email: 'новый@site.com', role: 'donors' });
  });

  it.each([
    ['у оператора включено', OPERATOR, true],
    ['у роли «Только доноры» выключено', DONORS_ONLY, false],
  ])('право видно в окне прав: %s', async (_, who, granted) => {
    await openUsers();
    const row = screen.getByText(who.email).closest('tr') as HTMLElement;

    await userEvent.setup().click(within(row).getByRole('button', { name: /^Права/ }));

    const right = await screen.findByRole('switch', { name: 'Рекламодатели' });
    expect((right as HTMLInputElement).checked).toBe(granted);
  });
});
