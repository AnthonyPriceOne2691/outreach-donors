/**
 * «Применить советы судьи»: итог очереди одной строкой, окно с планом, ход по порциям, итог,
 * остановка и откат партии. Проверяется то, чем экран может соврать: применить, не показав план;
 * бросить партию на первой порции; пообещать письма у «пишем»; откатить без подтверждения; выдать
 * решение судьи за решение человека.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type {
  JudgeApplied,
  JudgeBatch,
  JudgePlan,
  JudgeStanding,
} from '../api/advertisersJudgeApply';
import { actionTitle, detailLines, targetParts } from '../api/auditLabels';
import { CRAWL_ROUTES, OPERATOR, PROMOTE_ROUTES, reviewQueue, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import { JudgeApplyButton, JudgeApplyLine } from './JudgeApply';
import { standingWords } from './judgeApplyWords';

const URL = '/api/advertisers/judge-apply';
const BATCH = 'skip-20261111-090000-a1b2';

const STANDING: JudgeStanding = {
  waiting: 250,
  skip: 120,
  write: 80,
  waiting_dr: 0,
  unclear: 30,
  stale: 0,
  without: 20,
};

const SKIP_PLAN: JudgePlan = {
  band: 'skip',
  pairs: 3,
  donors: 2,
  rules: [
    { rule: 'free_directory', words: 'бесплатная карточка каталога', pairs: 2 },
    { rule: 'navigation', words: 'ссылка — в меню, шапке, подвале или виджете сайта', pairs: 1 },
  ],
  examples: Array.from({ length: 7 }, (_, at) => ({
    donor_host: 'donor.example.test',
    target_root: `site-${at}.example.test`,
    points: 2,
    rule: 'free_directory',
    words: 'бесплатная карточка каталога',
  })),
  standing: { ...STANDING, waiting: 4, skip: 3, write: 1, unclear: 0, without: 0 },
  takes_off: 0,
  launch: null,
  portion: 2,
};

const WRITE_PLAN: JudgePlan = {
  band: 'write',
  pairs: 5,
  donors: 3,
  rules: [{ rule: 'promoted', words: 'домен — бизнес, ссылка его продвигает', pairs: 5 }],
  examples: [],
  standing: { ...STANDING, waiting: 10, skip: 3, write: 5, waiting_dr: 2, unclear: 0, without: 0 },
  takes_off: 0,
  launch: { domains: 5, fresh: 4, with_address: 1 },
  portion: 200,
};

const NO_PORTION: Omit<JudgeApplied, 'applied' | 'left'> = {
  batch: BATCH,
  band: 'skip',
  decided: 0,
  gone: 0,
  removed: 0,
  stopped: 0,
  answered: [],
  answered_total: 0,
};

const APPLIED_BATCH: JudgeBatch = {
  id: BATCH,
  band: 'skip',
  by: 'оператор@site.com',
  started_at: '2026-11-11T09:00:00+00:00',
  applied: 3,
  left: 0,
  standing: 3,
  reverted_at: null,
  reverted_by: null,
  returned: 0,
};

function open(routes: Record<string, unknown> = {}, who: unknown = OPERATOR) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    // Окно спрашивает и ночное автоприменение своей полосы (`JudgeNight`): в общих ответах
    // «Проверки» оно выключено.
    ...PROMOTE_ROUTES,
    'GET /api/auth/me': { body: who },
    [`GET ${URL}`]: { body: STANDING },
    [`GET ${URL}/plan?band=skip`]: { body: SKIP_PLAN },
    [`GET ${URL}/plan?band=write`]: { body: WRITE_PLAN },
    [`GET ${URL}/batches`]: { body: [] },
    ...(routes as Record<string, never>),
  });
  renderWith(
    <>
      <JudgeApplyButton />
      <JudgeApplyLine />
    </>,
  );
  return recorded;
}

async function openDialog(routes: Record<string, unknown> = {}) {
  const recorded = open(routes);
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Применить советы…' }));
  const dialog = await screen.findByRole('dialog', { name: 'Применить советы судьи' });
  return { recorded, user, dialog };
}

function posts(calls: Call[]): unknown[] {
  return calls.filter((call) => call.method === 'POST').map((call) => call.body);
}

describe('советы судьи над очередью', () => {
  it('итог — одной строкой, числа сходятся в «ждут человека»', async () => {
    open();

    expect(
      await screen.findByText(
        'Советы судьи: не пишем 120 · пишем 80 · не понять 30 · без совета 20',
      ),
    ).toBeInTheDocument();
  });

  it('«ждут DR» и «совет устарел» названы, только когда они не ноль', () => {
    expect(standingWords(STANDING)).not.toMatch(/DR|устарел/);

    const words = standingWords({ ...STANDING, waiting_dr: 12, stale: 5 });

    expect(words).toMatch(/ждут\sDR\s12 · совет\sустарел\s5$/);
    // Число не отрывается от слова: между ними неразрывный пробел.
    expect(words).toContain('не\u00a0пишем\u00a0120');
  });

  it('очередь пуста — строки нет', async () => {
    open({ [`GET ${URL}`]: { body: { ...STANDING, waiting: 0 } } });

    expect(await screen.findByRole('button', { name: 'Применить советы…' })).toBeInTheDocument();
    expect(screen.queryByText(/Советы судьи:/)).toBeNull();
  });

  it('без права запуска или решения — числа есть, кнопки нет, кто применяет — в «i»', async () => {
    open({}, { ...OPERATOR, permissions: ['view', 'advertisers', 'run'] });
    const user = userEvent.setup();

    expect(await screen.findByText(/Советы судьи: не пишем 120/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Применить советы…' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Что значат советы судьи' }));

    const hint = await screen.findByRole('dialog', { name: 'Что значат советы судьи' });
    expect(within(hint).getByText(/Применяет сотрудник с правами/)).toBeInTheDocument();
  });
});

describe('окно применения советов', () => {
  it('сначала план: правила, примеры свёрнуты, решений до нажатия нет', async () => {
    const { recorded, user, dialog } = await openDialog();

    expect(await within(dialog).findByText('3 пары у 2 доноров')).toBeInTheDocument();
    expect(within(dialog).getByText(/Пары уйдут из очереди/)).toBeInTheDocument();
    expect(within(dialog).getByText('бесплатная карточка каталога — 2')).toBeInTheDocument();
    const examples = within(dialog).getByRole('list', { name: 'Примеры пар полосы' });
    expect(within(examples).getAllByRole('listitem')).toHaveLength(5);
    expect(posts(recorded.calls)).toEqual([]);

    await user.click(within(dialog).getByRole('button', { name: 'Показать все · 7' }));

    expect(within(examples).getAllByRole('listitem')).toHaveLength(7);
    expect(
      within(examples).getByText(/site-6\.example\.test · балл\s2 — бесплатная/),
    ).toBeVisible();
    expect(within(dialog).getByRole('button', { name: 'Свернуть' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('экран шлёт порции той же партии, пока не останется ноль', async () => {
    let asked = 0;
    const { recorded, user, dialog } = await openDialog({
      [`POST ${URL}`]: () => {
        asked += 1;
        return { body: { ...NO_PORTION, applied: asked === 1 ? 2 : 1, left: asked === 1 ? 1 : 0 } };
      },
      [`GET ${URL}/batches`]: () => ({ body: asked > 0 ? [APPLIED_BATCH] : [] }),
    });

    await user.click(
      await within(dialog).findByRole('button', { name: 'Применить: не пишем · 3' }),
    );

    expect(await within(dialog).findByText('Применено')).toBeInTheDocument();
    expect(within(dialog).getByText('Решений «не пишем» поставлено: 3')).toBeInTheDocument();
    // Первая порция начинает партию — имя даёт сервер; вторая идёт с ним.
    expect(posts(recorded.calls)).toEqual([
      { band: 'skip', limit: 2 },
      { band: 'skip', batch: BATCH, limit: 2 },
    ]);
    // Партия встала в список — её можно откатить тут же.
    expect(await within(dialog).findByText(/применено\s3/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /^Откатить партию/ })).toBeEnabled();
  });

  it('«Остановить» встаёт после летевшей порции и говорит, сколько осталось', async () => {
    const { recorded, user, dialog } = await openDialog({
      [`POST ${URL}`]: { body: { ...NO_PORTION, applied: 2, left: 1 } },
    });
    // Первая порция летит, пока тест не отпустит: за это время человек успевает нажать.
    let release = (): void => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const answer = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (init?.method === 'POST') await held;
      return answer(input, init);
    });

    await user.click(
      await within(dialog).findByRole('button', { name: 'Применить: не пишем · 3' }),
    );
    expect(await within(dialog).findByRole('status')).toHaveTextContent('Применяем: 0 из 3');
    // Пока порции идут, окно не закрыть: молчаливое закрытие читалось бы как «применилось всё».
    expect(within(dialog).queryByRole('button', { name: 'Закрыть' })).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: 'Остановить' }));
    release();

    expect(await within(dialog).findByText('Остановлено')).toBeInTheDocument();
    expect(within(dialog).getByText('Решений «не пишем» поставлено: 2')).toBeInTheDocument();
    expect(within(dialog).getByText(/^Осталось: 1 — «Применить» продолжит/)).toBeInTheDocument();
    expect(posts(recorded.calls)).toHaveLength(1);
  });

  it('отказ сервера посреди партии — словами, с тем, что уже применено', async () => {
    let asked = 0;
    const { dialog, user } = await openDialog({
      [`POST ${URL}`]: () => {
        asked += 1;
        if (asked === 1) return { body: { ...NO_PORTION, applied: 2, left: 1 } };
        return {
          status: 409,
          body: { detail: 'Партия откатана и не продолжается: начните новую' },
        };
      },
    });

    await user.click(
      await within(dialog).findByRole('button', { name: 'Применить: не пишем · 3' }),
    );

    expect(await within(dialog).findByText('Применение оборвалось')).toBeInTheDocument();
    expect(within(dialog).getByText(/Партия откатана и не продолжается/)).toBeInTheDocument();
    expect(within(dialog).getByText('Решений «не пишем» поставлено: 2')).toBeInTheDocument();
  });

  it('у «пишем» окно не обещает писем: перевод и сборка — руками', async () => {
    const { user, dialog, recorded } = await openDialog({
      [`POST ${URL}`]: {
        body: {
          ...NO_PORTION,
          batch: 'write-20261111-090000-c3d4',
          band: 'write',
          applied: 5,
          left: 0,
        },
      },
    });

    await user.click(within(dialog).getByRole('radio', { name: /^Пишем · / }));

    const effects = await within(dialog).findByRole('list', { name: 'Что произойдёт' });
    expect(effects).toHaveTextContent('после «Перевести» в блоке «К письму»: новых 4 из 5');
    expect(effects).toHaveTextContent('Адрес уже есть у 1');
    expect(effects).toHaveTextContent('Письма сами не уйдут');
    expect(effects).toHaveTextContent('Ещё 2 «пишем» ждут DR');

    await user.click(within(dialog).getByRole('button', { name: 'Применить: пишем · 5' }));

    expect(await within(dialog).findByText('Решений «пишем» поставлено: 5')).toBeInTheDocument();
    expect(within(dialog).getByText(/^Дальше — «Перевести»/)).toBeInTheDocument();
    expect(posts(recorded.calls)).toEqual([{ band: 'write', limit: 200 }]);
  });

  it('снятие рекламодателей у «не пишем» названо до нажатия', async () => {
    const { dialog } = await openDialog({
      [`GET ${URL}/plan?band=skip`]: { body: { ...SKIP_PLAN, takes_off: 2 } },
    });

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('Снимутся рекламодатели');
    expect(alert).toHaveTextContent(/У доменов:\s2/);
  });

  it('применять нечего — окно объясняет, а список партий на месте', async () => {
    const { dialog } = await openDialog({
      [`GET ${URL}/plan?band=skip`]: { body: { ...SKIP_PLAN, pairs: 0, rules: [], examples: [] } },
      [`GET ${URL}/batches`]: { body: [APPLIED_BATCH] },
    });

    expect(await within(dialog).findByText(/Пар с советом «не пишем» в очереди нет/)).toBeVisible();
    expect(within(dialog).getByRole('button', { name: 'Применить: не пишем · 0' })).toBeDisabled();
    expect(await within(dialog).findByText(/в силе\s3/)).toBeInTheDocument();
  });
});

describe('партии и откат', () => {
  it('откат — с подтверждением у кнопки, итог — числами в окне', async () => {
    let reverted = false;
    const { recorded, user, dialog } = await openDialog({
      [`GET ${URL}/batches`]: () => ({
        body: [
          reverted
            ? {
                ...APPLIED_BATCH,
                standing: 0,
                reverted_at: '2026-11-11T10:00:00+00:00',
                returned: 2,
              }
            : APPLIED_BATCH,
        ],
      }),
      [`POST ${URL}/batches/${BATCH}/revert`]: () => {
        reverted = true;
        return {
          body: { batch: BATCH, returned: 2, kept: 1, forgotten: 0, removed: 0, stopped: 0 },
        };
      },
    });

    await user.click(await within(dialog).findByRole('button', { name: /^Откатить партию/ }));
    // Первое нажатие ничего не откатывает: вопрос называет, что вернётся.
    expect(
      await screen.findByText(/Решения партии вернутся в очередь проверки/),
    ).toBeInTheDocument();
    expect(posts(recorded.calls)).toEqual([]);

    await user.click(await screen.findByRole('button', { name: 'Откатить' }));

    expect(await within(dialog).findByText('Партия откатана')).toBeInTheDocument();
    expect(within(dialog).getByText('В очередь вернулось: 2')).toBeInTheDocument();
    expect(within(dialog).getByText('Письмо уже ушло — решение осталось: 1')).toBeInTheDocument();
    await waitFor(() =>
      expect(within(dialog).queryByRole('button', { name: /^Откатить партию/ })).toBeNull(),
    );
    expect(within(dialog).getByText(/^оператор@site\.com · откатана /)).toBeInTheDocument();
  });

  it('откатывать нечего — отказ сервера словами в окне', async () => {
    const { user, dialog } = await openDialog({
      [`GET ${URL}/batches`]: { body: [APPLIED_BATCH] },
      [`POST ${URL}/batches/${BATCH}/revert`]: {
        status: 409,
        body: { detail: 'Откатывать нечего: решения партии уже возвращены в очередь' },
      },
    });

    await user.click(await within(dialog).findByRole('button', { name: /^Откатить партию/ }));
    await user.click(await screen.findByRole('button', { name: 'Откатить' }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('Партия не откатана');
    expect(alert).toHaveTextContent('Откатывать нечего');
  });

  it('длинный список партий свёрнут до трёх с числом', async () => {
    const many = Array.from({ length: 5 }, (_, at) => ({
      ...APPLIED_BATCH,
      id: `skip-2026111${at}-090000-a1b2`,
      started_at: `2026-11-1${at}T09:00:00+00:00`,
    }));
    const { user, dialog } = await openDialog({ [`GET ${URL}/batches`]: { body: many } });

    expect(await within(dialog).findAllByRole('button', { name: /^Откатить партию/ })).toHaveLength(
      3,
    );

    await user.click(within(dialog).getByRole('button', { name: 'Показать все · 5' }));

    expect(within(dialog).getAllByRole('button', { name: /^Откатить партию/ })).toHaveLength(5);
  });
});

describe('решение судьи в очереди', () => {
  const said = {
    advice: 'skip' as const,
    words: 'бесплатная карточка каталога',
    link_quote: 'listed in our directory',
    target_quote: '',
    disclosure: '',
    prompt_version: 'advertiser-judge-v3',
    model: 'gpt-5-mini',
    judged_at: '2026-11-10T12:00:00Z',
  };
  const row = {
    id: 12,
    donor_host: 'donor.example.test',
    target_root: 'advertiser.example',
    points: 3,
    verdict: 'pending' as const,
    reasons: ['коммерческий анкор +1'],
    links: 2,
    pages: 2,
    best_page_url: 'https://donor.example.test/post/1',
    best_anchor: 'Bet now',
    confirmed: false,
    decided_by: `судья:${BATCH}`,
    decided_at: '2026-11-11T09:00:00Z',
    by_judge: true,
    judge: said,
  };

  async function openQueue(rows: unknown[]) {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      ...CRAWL_ROUTES,
      'GET /api/auth/me': { body: OPERATOR },
      'GET /api/advertisers/niche?page=1': {
        body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
      },
      'GET /api/advertisers?include_decided=true': {
        body: reviewQueue({ rows, counts: { pending: rows.length } }),
      },
      'POST /api/advertisers/12/decide': {
        body: { ...row, confirmed: true, by_judge: false, decided_by: OPERATOR.email },
      },
    });
    renderWith(<AppRoutes />, '/advertisers?view=review&include_decided=true');
    await screen.findByText('advertiser.example');
    return recorded;
  }

  it('видно, что решил судья и по какому правилу, а не метка партии', async () => {
    await openQueue([row]);

    expect(screen.getByText('не пишем — решил судья')).toBeInTheDocument();
    expect(
      screen.getByText('правило: бесплатная карточка каталога · «listed in our directory»'),
    ).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(BATCH))).toBeNull();
  });

  it('совет с тех пор сменился — он назван рядом с решением судьи', async () => {
    await openQueue([{ ...row, judge: { ...said, advice: 'write', words: 'домен — бизнес' } }]);

    expect(screen.getByText('не пишем — решил судья')).toBeInTheDocument();
    expect(screen.getByText(/^судья: пишем — домен — бизнес/)).toBeInTheDocument();
  });

  it('человек переигрывает решение судьи обычной кнопкой', async () => {
    const recorded = await openQueue([row]);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Пишем' }));

    await waitFor(() => expect(posts(recorded.calls)).toEqual([{ confirmed: true, force: true }]));
  });
});

describe('партии в журнале действий', () => {
  it('порция и откат названы словами, полоса — словами экрана', () => {
    const target = `judge_batch:${BATCH}`;

    expect(actionTitle('advertiser_judge_applied', target)).toBe('Советы судьи применены');
    expect(actionTitle('advertiser_judge_reverted', target)).toBe('Партия советов судьи откатана');
    expect(targetParts(target, new Map())).toEqual([{ text: `партия советов судьи ${BATCH}` }]);
    expect(detailLines({ полоса: 'skip', применено: 200, осталось: 390 })).toEqual([
      { label: 'полоса', value: 'не пишем' },
      { label: 'применено', value: '200' },
      { label: 'осталось', value: '390' },
    ]);
  });
});
