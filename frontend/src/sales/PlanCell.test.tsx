/**
 * Колонка «План работ» и строка плана на вкладке «Гипотезы». Ответы сервера — записанные
 * (`serve()`); имена, числа и ключи выдуманы.
 *
 * Проверяется то, ради чего колонка: у гипотезы плана — этап словами, «найдено N из цели» с
 * полосой, расход из потолка, проблемы строками, кнопки по этапу и ссылки на «Сбор базы» и
 * «Воронку» с гипотезой; без плана колонки нет; строка плана — сколько готово и расход, кнопка
 * «Остановить план» или «Продолжить» с причиной паузы; кнопка уходит на сервер, отказ — словами
 * сервера; список планов опрашивается, только пока план идёт. Ширины колонок с планом — по самому
 * длинному в них, и таблица встаёт рядом с открытым меню на 1440; «Потолок» у строки плана открывает
 * окно потолка плана; полоса — дорожкой своего цвета и
 * без розы у набравшей цель; имена кнопок для читалки — с именем гипотезы; на телефоне в ячейке
 * плана — строка с именем.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { COLUMNS, HypothesesTable, tableWidth, WITH_PLAN } from './HypothesesTable';
import { PLAN_POLL_MS } from './planApi';
import type { PlanLive, PlanRow } from './planApi';
import { stageWords, waveDepth } from './planWords';

type Route = Answer | ((call: Call) => Answer);

const PLANS_ROUTE = 'GET /api/sales/plans';

function card(id: number, name: string): HypothesisCard {
  return {
    id,
    name,
    description: null,
    created_at: '2026-11-16T09:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
  };
}

const LOGISTICS = card(11, 'Логистика — пример');
const DENTAL = card(12, 'Стоматологии — пример');
const OUTSIDE = card(13, 'Своя гипотеза');

function row(overrides: Partial<PlanRow> = {}): PlanRow {
  return {
    id: 1,
    hypothesis_id: LOGISTICS.id,
    name: LOGISTICS.name,
    position: 1,
    sheet: 'Пилот 1',
    markets: ['kz'],
    language: 'ru',
    target: 5000,
    max_pages: 5,
    cap_usd: 50,
    stage: 'collecting',
    found: 1240,
    spent_usd: 12.4,
    keywords: 30_000,
    bought: 30_000,
    wave_page: 2,
    wave_bought: 1200,
    reserved_usd: 0.9,
    problems: [],
    ...overrides,
  };
}

function plan(overrides: Partial<PlanLive> = {}): PlanLive {
  return {
    id: 7,
    name: 'План недели',
    mode: 'economy',
    target_default: null,
    cap_usd: 100,
    status: 'running',
    problem: null,
    created_by: 'admin@ours.example.test',
    created_at: '2026-11-16T12:00:00+00:00',
    hypotheses: [
      row(),
      row({
        id: 2,
        hypothesis_id: DENTAL.id,
        name: DENTAL.name,
        position: 2,
        stage: 'done',
        found: 300,
        target: 300,
        spent_usd: 7.6,
      }),
    ],
    ...overrides,
  };
}

function setUp(plans: PlanLive[], routes: Record<string, Route> = {}): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  return serve({
    'GET /api/auth/me': { body: ADMIN },
    [PLANS_ROUTE]: { body: { plans } },
    ...routes,
  });
}

/** Ячейка плана — вторая, сразу за именем (стенд 17.11: последней её не было видно на 1280). */
function cellOf(name: string): HTMLElement {
  const line = screen.getByRole('row', { name: new RegExp(name) });
  const plan = within(line).getAllByRole('cell').at(1);
  if (plan === undefined) throw new Error(`у строки «${name}» нет ячейки плана`);
  return plan;
}

function asked(recorded: Recorded, path: string, method = 'GET'): Call[] {
  return recorded.calls.filter((call) => call.path === path && call.method === method);
}

/** Пробелы разрядов и перед знаком денег — неразрывные; сверка — обычными. */
function plain(text: string): string {
  return text.replace(/\s/g, ' ');
}

afterEach(() => {
  vi.useRealTimers();
});

describe('слова колонки', () => {
  it('сбор — глубина волны и купленные ключи; волна 4 — до предела гипотезы', () => {
    expect(plain(stageWords(row()))).toBe('сбор: топ-20, ключи 1 200 из 30 000');
    expect(waveDepth({ wave_page: 4, max_pages: 5 })).toBe(5);
    expect(waveDepth({ wave_page: 3, max_pages: 2 })).toBe(2);
    expect(
      ['waiting', 'sending', 'done', 'stopped'].map((stage) => stageWords(row({ stage }))),
    ).toEqual(['ждёт очереди', 'письма', 'готово', 'остановлена']);
  });
});

/** Обёртка таблицы на 1440 × 900 с открытым меню (стенд 17.11): шире — таблица уезжает вбок. */
const AREA_1440 = 1090;
/** Самое длинное в колонке по живому тексту (стенд 18.11: «1 000 000» ссылкой — 69,4 px, дата
 *  табличными цифрами — 77,4, шапка «Отклонены» — 79,4) и поля ячейки (2 × 16 px). */
const NEEDED: Record<string, number> = {
  Новые: 69.4 + 32,
  Готовы: 69.4 + 32,
  Отклонены: 79.4 + 32,
  Всего: 69.4 + 32,
  Заведена: 77.4 + 32,
};

describe('ширины колонок', () => {
  it('с планом — встаёт в карточку на 1440 с открытым меню, «1 000 000» и дата не режутся', () => {
    expect(tableWidth(WITH_PLAN)).toBeLessThanOrEqual(AREA_1440);
    const counts = WITH_PLAN.filter(({ title }) => title in NEEDED);
    expect(counts.map(({ title }) => title)).toEqual(Object.keys(NEEDED));
    for (const { title, width } of counts) {
      expect(parseFloat(width ?? '0') * 16, title).toBeGreaterThanOrEqual(NEEDED[title] ?? 0);
    }
  });

  it('без плана таблица прежняя — 912 px', () => {
    expect(tableWidth(COLUMNS)).toBe(912);
  });
});

describe('колонка «План работ»', () => {
  it('у гипотезы плана — этап, найдено с полосой, расход, проблемы и ссылки; у своей — пусто', async () => {
    const keys = 'ключи и глубина до топ-50 кончились: найдено 300 из 300 — добавьте ключи';
    setUp([
      plan({
        hypotheses: [
          row({
            problems: [
              { code: 'run_failed', words: 'прогон не поставлен: потолок модели', at: null },
            ],
          }),
          row({
            id: 2,
            hypothesis_id: DENTAL.id,
            name: DENTAL.name,
            stage: 'stopped',
            problems: [{ code: 'keys', words: keys, at: null }],
          }),
        ],
      }),
    ]);
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL, OUTSIDE]} />);

    expect(await screen.findByRole('columnheader', { name: 'План работ' })).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader').map((head) => head.textContent)).toEqual([
      'Гипотеза',
      'План работ',
      'Новые',
      'Готовы',
      'Отклонены',
      'Всего',
      'Заведена',
    ]);
    const cell = cellOf(LOGISTICS.name);
    expect(cell).toHaveTextContent('Сбор: топ-20, ключи 1 200 из 30 000');
    expect(cell).toHaveTextContent('найдено 1 240 из 5 000');
    expect(
      within(cell).getByRole('progressbar', {
        name: (name) => plain(name) === `«${LOGISTICS.name}»: найдено 1 240 из 5 000`,
      }),
    ).toHaveAttribute('aria-valuenow', '25');
    expect(cell).toHaveTextContent('расход 12,40 $ из 50,00 $');
    expect(cell).toHaveTextContent('Прогон не поставлен: потолок модели');
    expect(
      within(cell).getByRole('link', { name: `Прогоны гипотезы «${LOGISTICS.name}»` }),
    ).toHaveAttribute('href', '/sales?tab=collect&hypothesis=11');
    expect(
      within(cell).getByRole('link', { name: `Воронка гипотезы «${LOGISTICS.name}»` }),
    ).toHaveAttribute('href', '/sales?tab=funnel&hypothesis=11');
    expect(
      within(cell).getByRole('button', { name: `Остановить гипотезу «${LOGISTICS.name}»` }),
    ).toBeInTheDocument();
    expect(within(cell).queryByRole('button', { name: /^Добавить ключи/ })).toBeNull();
    const stopped = cellOf(DENTAL.name);
    expect(
      within(stopped).getByRole('button', { name: `Продолжить гипотезу «${DENTAL.name}»` }),
    ).toBeInTheDocument();
    expect(
      within(stopped).getByRole('button', { name: `Добавить ключи гипотезе «${DENTAL.name}»` }),
    ).toBeInTheDocument();
    expect(cellOf(OUTSIDE.name)).toBeEmptyDOMElement();
  });

  it('таблица не уже суммы своих колонок: с планом — колонок с планом', async () => {
    setUp([plan()]);
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);
    const table = await screen.findByRole('table');
    await screen.findByRole('columnheader', { name: 'План работ' });

    const wrap = table.closest('.mantine-TableScrollContainer-scrollContainer');
    // Mantine пишет ширину в `rem`: 1 088 px — 68rem.
    expect(wrap?.getAttribute('style')).toContain(`${tableWidth(WITH_PLAN) / 16}rem`);
  });

  it('полоса «найдено» — дорожкой своего цвета, на свету — глубокой заливкой; набравшая цель — не розой', async () => {
    setUp([plan()]);
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);
    await screen.findByRole('columnheader', { name: 'План работ' });

    for (const name of [LOGISTICS.name, DENTAL.name]) {
      const bar = within(cellOf(name)).getByRole('progressbar');
      const track = bar.closest('.meter');
      expect(track, name).not.toBeNull();
      expect(track).not.toHaveAttribute('data-tone');
      // Заливка — ступень `.meter` по теме (`--meter-fill`): на свету глубокая — акцент к своей
      // дорожке давал 2,42 : 1 при норме 3 (стенд 18.11).
      expect(bar.getAttribute('style'), name).toContain('var(--meter-fill)');
    }
    // Глубокая ступень на свету — правилом `.meter` в `glass.css` (#645), а не выбором в ячейке.
    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');
    expect(glass).toMatch(
      /html:root\[data-mantine-color-scheme='light'\] \.meter \{\s*--meter-fill: var\(--accent-deep\);/,
    );
    // Набравшая цель — 300 из 300: полоса полная.
    expect(within(cellOf(DENTAL.name)).getByRole('progressbar')).toHaveAttribute(
      'aria-valuenow',
      '100',
    );
  });

  it('имена кнопок и ссылок для читалки — с именем гипотезы, видимое слово — их начало', async () => {
    setUp([
      plan({ hypotheses: [row({ problems: [{ code: 'keys', words: 'нужны ключи', at: null }] })] }),
    ]);
    renderWith(<HypothesesTable rows={[LOGISTICS]} />);
    await screen.findByRole('columnheader', { name: 'План работ' });
    const cell = cellOf(LOGISTICS.name);

    const controls = [...within(cell).getAllByRole('button'), ...within(cell).getAllByRole('link')];
    expect(controls.map((control) => control.textContent)).toEqual([
      'Остановить',
      'Качество сайтов',
      'Собрать ещё',
      'Добавить ключи',
      'Прогоны',
      'Воронка',
    ]);
    for (const control of controls) {
      const name = control.getAttribute('aria-label') ?? '';
      expect(name.startsWith(control.textContent ?? '—'), name).toBe(true);
      expect(name).toContain(`«${LOGISTICS.name}»`);
    }
  });

  it('на телефоне в ячейке плана — строка с именем гипотезы; читалке — нет, имя в первой ячейке', async () => {
    setUp([plan()]);
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);
    await screen.findByRole('columnheader', { name: 'План работ' });

    const line = within(cellOf(LOGISTICS.name)).getByText(LOGISTICS.name);
    // `hiddenFrom="sm"`: от 48em строки нет — там имя закреплено и видно рядом.
    expect(line).toHaveClass('mantine-hidden-from-sm');
    expect(line).toHaveAttribute('aria-hidden', 'true');
    expect(line.parentElement?.firstElementChild).toBe(line);
  });

  it('без плана колонки нет — таблица прежняя', async () => {
    const recorded = setUp([]);
    renderWith(<HypothesesTable rows={[OUTSIDE]} />);

    await waitFor(() => expect(asked(recorded, '/api/sales/plans')).toHaveLength(1));
    expect(screen.queryByRole('columnheader', { name: 'План работ' })).toBeNull();
    expect(screen.queryByText(/План «/)).toBeNull();
  });

  it('планы не пришли — строкой словами, таблица гипотез на месте', async () => {
    setUp([], { [PLANS_ROUTE]: { status: 503, body: { detail: 'база не ответила' } } });
    renderWith(<HypothesesTable rows={[OUTSIDE]} />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Планы работ не загрузились: база не ответила',
    );
    expect(screen.getByRole('row', { name: /Своя гипотеза/ })).toBeInTheDocument();
  });

  it('«Остановить» уходит на сервер, отказ — словами под кнопками', async () => {
    const refusal = 'гипотеза «Логистика — пример» уже остановлена';
    const recorded = setUp([plan()], {
      'POST /api/sales/plan-hypotheses/1/stop': { status: 409, body: { detail: refusal } },
    });
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);

    await userEvent
      .setup()
      .click(
        await screen.findByRole('button', { name: `Остановить гипотезу «${LOGISTICS.name}»` }),
      );

    const cell = cellOf(LOGISTICS.name);
    const alert = await within(cell).findByRole('alert');
    expect(alert).toHaveTextContent(refusal);
    expect(asked(recorded, '/api/sales/plan-hypotheses/1/stop', 'POST')).toHaveLength(1);
    // Под рядом кнопок, а не в нём (стенд 17.11: отказ вставал между кнопками).
    const more = within(cell).getByRole('button', { name: /^Собрать ещё/ });
    expect(alert.parentElement).not.toBe(more.parentElement);
    expect(more.compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('окно кнопки закрыли — фокус снова на кнопке, место в таблице не теряется', async () => {
    setUp([plan()]);
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);
    const user = userEvent.setup();
    await screen.findByRole('columnheader', { name: 'План работ' });
    const more = within(cellOf(LOGISTICS.name)).getByRole('button', {
      name: `Собрать ещё гипотезе «${LOGISTICS.name}»`,
    });

    await user.click(more);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(more).toHaveFocus());
  });
});

describe('строка плана', () => {
  it('сколько пилотов готово и расход; «Остановить план» уходит и список перечитывается', async () => {
    const recorded = setUp([plan()], {
      'POST /api/sales/plans/7/stop': {
        body: plan({ status: 'paused', problem: 'план остановлен кнопкой' }),
      },
    });
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);

    expect(
      await screen.findByText(
        'План «План недели»: готово 1 пилот из 2 · расход 20,00 $ из 100,00 $',
      ),
    ).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'Остановить план «План недели»' }));

    await waitFor(() => expect(asked(recorded, '/api/sales/plans')).toHaveLength(2));
    expect(asked(recorded, '/api/sales/plans/7/stop', 'POST')).toHaveLength(1);
  });

  it('«Потолок» открывает окно потолка плана; поднятый уходит и список перечитывается', async () => {
    const recorded = setUp([plan()], {
      'PATCH /api/sales/plans/7/cap': { body: plan({ cap_usd: 150 }) },
    });
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Потолок плана «План недели»' }));
    const cap = within(await screen.findByRole('dialog')).getByLabelText('Потолок плана, $');
    await user.clear(cap);
    await user.type(cap, '150');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(asked(recorded, '/api/sales/plans/7/cap', 'PATCH').map((call) => call.body)).toEqual([
      { cap_usd: 150 },
    ]);
    await waitFor(() => expect(asked(recorded, '/api/sales/plans')).toHaveLength(2));
  });

  it('на паузе — причина словами и «Продолжить»; окончённый план строки не держит', async () => {
    const words = 'счёт выдачи мал: на счету 0,10 $ — пополните счёт';
    setUp([
      plan({ status: 'paused', problem: words }),
      plan({ id: 8, name: 'Старый план', status: 'done', hypotheses: [] }),
    ]);
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);

    expect(
      await screen.findByText('Счёт выдачи мал: на счету 0,10 $ — пополните счёт'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Продолжить план «План недели»' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Старый план/)).toBeNull();
  });
});

describe('опрос', () => {
  it('идёт план — список перечитывается раз в 20 с; встал — опрос снят', async () => {
    const sets = vi.spyOn(globalThis, 'setInterval');
    const clears = vi.spyOn(globalThis, 'clearInterval');
    /** Интервалы опроса, которые стоят сейчас: поставлены и не сняты. */
    const polling = () => {
      const cleared = new Set<unknown>(clears.mock.calls.map(([id]) => id));
      return sets.mock.calls
        .map(([tick, delay], index) => {
          const id: unknown = sets.mock.results[index]?.value;
          return { tick, delay, id };
        })
        .filter(({ delay, id }) => delay === PLAN_POLL_MS && !cleared.has(id));
    };
    let answer = plan();
    const recorded = setUp([], { [PLANS_ROUTE]: () => ({ body: { plans: [answer] } }) });
    renderWith(<HypothesesTable rows={[LOGISTICS, DENTAL]} />);
    await screen.findByText(/План «План недели»/);

    const ticking = polling();
    expect(ticking).toHaveLength(1);
    answer = plan({ status: 'paused', problem: 'план остановлен кнопкой' });
    act(() => {
      for (const { tick } of ticking) (tick as () => void)();
    });
    await screen.findByText('План остановлен кнопкой');

    expect(asked(recorded, '/api/sales/plans')).toHaveLength(2);
    expect(polling()).toHaveLength(0);
  });
});
