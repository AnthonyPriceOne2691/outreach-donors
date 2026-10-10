/**
 * Пороги: предпросмотр до сохранения и новая версия вместо правки.
 *
 * Проверяется то, ради чего экран такой: человек видит последствия
 * до нажатия, а сохранение не переписывает прошлые вердикты.
 */

import { act, screen, waitFor } from '@testing-library/react';
import { Profiler } from 'react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

/** Адрес запроса из того, что отдали в `fetch`. */
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
}
const CURRENT = {
  version: 2,
  created_by: 'ivan@site.com',
  created_at: '2026-09-18T10:00:00+00:00',
  min_dr: 20,
  min_org_traffic: 500,
  min_refdomains: 100,
  min_keywords: 300,
};

const LIMITS = {
  min_dr: { min: 0, max: 90 },
  min_org_traffic: { min: 0, max: 10_000_000 },
  min_refdomains: { min: 0, max: 1_000_000 },
  min_keywords: { min: 0, max: 1_000_000 },
};

const VIEW = {
  current: CURRENT,
  defaults: { min_dr: 20, min_org_traffic: 500, min_refdomains: 100, min_keywords: 300 },
  history: [CURRENT],
  limits: LIMITS,
};

const CONSEQUENCES = {
  checked: 111,
  suitable_now: 105,
  suitable_after: 92,
  falls_out: 13,
  falls_out_with_price: 4,
  comes_back: 0,
  unchecked: 6,
};

async function openThresholds(routes: Record<string, unknown> = {}, view: unknown = VIEW) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/settings/thresholds': { body: view },
    'POST /api/settings/preview': { body: CONSEQUENCES },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/settings');
  await screen.findByRole('heading', { name: 'Пороги отбора' });
  return recorded;
}

/** Сдвинуть порог DR: последствия показываются только у изменённых порогов. */
async function touchDr() {
  const user = userEvent.setup();
  const dr = screen.getByLabelText('DR не ниже');
  await user.clear(dr);
  await user.type(dr, '25');
}

describe('пороги', () => {
  it('нетронутые пороги не обещают перемен в базе', async () => {
    await openThresholds();

    expect(screen.getByText(/Пороги совпадают с действующими/)).toBeInTheDocument();
    expect(screen.queryByText('Выпадет из базы')).not.toBeInTheDocument();
  });

  it('показывает последствия до сохранения', async () => {
    await openThresholds();
    await touchDr();

    expect(await screen.findByText('13')).toBeInTheDocument();
    expect(screen.getByText('из них с ценой: 4')).toBeInTheDocument();
    // Домены без метрик считаются отдельно: пороги их не судят.
    expect(screen.getByText(/Без метрик — ещё 6 доменов/)).toBeInTheDocument();
  });

  // Проверка прода 10.10.2026: «Ещё 1 доменов без метрик» — число не согласовано со словом.
  it.each([
    [1, 'Без метрик — ещё 1 домен: пороги его не судят.'],
    [3, 'Без метрик — ещё 3 домена: пороги их не судят.'],
    [11, 'Без метрик — ещё 11 доменов: пороги их не судят.'],
    [21, 'Без метрик — ещё 21 домен: пороги их не судят.'],
  ])('без метрик %i — слово согласовано с числом', async (count, said) => {
    await openThresholds({
      'POST /api/settings/preview': { body: { ...CONSEQUENCES, unchecked: count } },
    });
    await touchDr();

    expect(await screen.findByText(new RegExp(`^${said}`))).toBeInTheDocument();
  });

  it('предупреждает, если выпадают доноры с полученной ценой', async () => {
    await openThresholds();
    await touchDr();

    expect(
      await screen.findByText('Среди выпавших есть доноры с полученной ценой'),
    ).toBeInTheDocument();
  });

  it('пока пороги не тронуты, сохранять нечего', async () => {
    await openThresholds();

    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
  });

  it('сохранение заводит новую версию', async () => {
    const recorded = await openThresholds({
      'POST /api/settings/thresholds': { body: { ...CURRENT, version: 3, min_dr: 25 } },
    });
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    await user.type(dr, '25');
    await user.click(screen.getByRole('button', { name: /Сохранить новой версией/ }));

    await waitFor(() => {
      expect(screen.getByText(/сохранены как версия 3/)).toBeInTheDocument();
    });
    const saved = recorded.calls.find(
      (call) => call.path === '/api/settings/thresholds' && call.method === 'POST',
    );
    expect((saved?.body as { min_dr: number }).min_dr).toBe(25);
  });

  it('отказ сервера на чтении — причина словами, а не вечная загрузка', async () => {
    // До 28.09.2026 проверка отказа стояла после ожидания черновика, а
    // черновик ждал ответа, которого не будет: значок крутился вечно.
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/settings/thresholds': { status: 503, body: { detail: 'База недоступна' } },
    });
    renderWith(<AppRoutes />, '/settings');

    expect(await screen.findByText('Пороги не загрузились')).toBeInTheDocument();
    expect(screen.getByText('База недоступна')).toBeInTheDocument();
    expect(screen.queryByLabelText('Загружаем пороги')).not.toBeInTheDocument();
  });

  it('без права на пороги правка закрыта, а история видна', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: { ...OPERATOR, permissions: ['view'] } },
      'GET /api/settings/thresholds': { body: VIEW },
    });
    renderWith(<AppRoutes />, '/settings');

    expect(await screen.findByText('Править пороги не разрешено')).toBeInTheDocument();
    expect(screen.getByLabelText('DR не ниже')).toBeDisabled();
    expect(screen.getByText('№2')).toBeInTheDocument();
  });
});

describe('пересчёт последствий', () => {
  it('не прячет прежние числа за значком загрузки: блок стоит приглушённым до ответа', async () => {
    let answers = 0;
    let release = () => {};
    await openThresholds({
      'POST /api/settings/preview': () => {
        answers += 1;
        return { body: CONSEQUENCES };
      },
    });
    await touchDr();
    expect(await screen.findByText('13')).toBeInTheDocument();

    const answered = vi.mocked(globalThis.fetch).getMockImplementation()!;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (urlOf(input).endsWith('/settings/preview')) await gate;
      return answered(input, init);
    });
    const user = userEvent.setup();
    // 25 → 26: порог в границах. До 28.09.2026 тест дописывал «0» и получал
    // DR 250 — экран отправлял его на сервер, хотя граница DR — 90.
    await user.type(screen.getByLabelText('DR не ниже'), '{backspace}6');

    await waitFor(() => expect(screen.getByText('13').closest('[data-stale]')).not.toBeNull());
    expect(screen.queryByLabelText('Считаем последствия')).not.toBeInTheDocument();

    release();
    await waitFor(() => expect(document.querySelector('[data-stale]')).toBeNull());
    expect(answers).toBeGreaterThanOrEqual(2);
  });
});

describe('границы порогов', () => {
  // Замечание 28.09.2026: «поставь валидацию на поля на допустимые значения
  // и добавь значки подсказок, какие значения допустимы».

  /** Переждать паузу набора (400 мс), после которой черновик уходит на
   *  предпросмотр: «не ушёл» проверяется после неё, а не до. */
  const pastDebounce = () =>
    act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
    });

  /** Запросы предпросмотра, ушедшие на сервер, — телами. */
  const previews = (recorded: { calls: { path: string; body: unknown }[] }) =>
    recorded.calls
      .filter((call) => call.path === '/api/settings/preview')
      .map((call) => call.body as Record<string, number>);

  it('порог за границей — отказ под полем, сохранять нечего, на сервер не уходит', async () => {
    const recorded = await openThresholds();
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    await user.type(dr, '95');

    expect(await screen.findByText('Допустимо от 0 до 90')).toBeInTheDocument();
    expect(dr).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
    // Блок сравнения не показывает прежний ответ как ответ на новый порог.
    expect(screen.getByText(/Поле «DR не ниже» не годится/)).toBeInTheDocument();
    await pastDebounce();
    expect(previews(recorded).some((body) => body.min_dr === 95)).toBe(false);
  });

  // Проверка прода 10.10.2026: «abc» в поле — под полем верно «Только целое число от 0
  // до 90», а в карточке «Порог вне допустимых границ» — другая причина, и неверная.
  // Карточка называет поле и отсылает к отказу под ним, а своей причины не выдумывает.
  it.each([
    ['abc', 'Только целое число от 0 до 90'],
    ['2.5', 'Только целое число от 0 до 90'],
    ['95', 'Допустимо от 0 до 90'],
    ['', 'Впишите число'],
  ])('«%s» в поле — карточка не спорит с отказом под полем', async (typed, refusal) => {
    await openThresholds();
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    if (typed !== '') await user.type(dr, typed);

    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(
      screen.getByText(
        'Поле «DR не ниже» не годится: что не так, сказано под ним. Поправьте его — и здесь появится сравнение с действующими.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/вне допустимых границ/)).not.toBeInTheDocument();
  });

  it('карточка называет поле с отказом, а не всегда DR', async () => {
    await openThresholds();
    const user = userEvent.setup();

    const keywords = screen.getByLabelText('Ключей в органике');
    await user.clear(keywords);
    await user.type(keywords, '1,5');

    expect(await screen.findByText(/Поле «Ключей в органике» не годится/)).toBeInTheDocument();
  });

  // Проверка QA 10.10.2026: числовое поле молча выбрасывало точку, запятую, минус и
  // буквы — «2.5» и «2,5» становились 25, «1e3» — 13, «-5» — 5, и такой порог уходил
  // в предпросмотр и в сохранение.
  it.each([
    ['2.5', 'Только целое число от 0 до 90'],
    ['2,5', 'Только целое число от 0 до 90'],
    ['1e3', 'Только целое число от 0 до 90'],
    ['-5', 'Допустимо от 0 до 90'],
  ])('«%s» стоит в поле как набрано, под ним — отказ', async (typed, refusal) => {
    const recorded = await openThresholds();
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    await user.type(dr, typed);

    expect(dr).toHaveValue(typed);
    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(dr).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
    await pastDebounce();
    expect(previews(recorded).some((body) => body.min_dr !== CURRENT.min_dr)).toBe(false);
  });

  it('стёртое поле — не ноль: просит число и не сохраняется', async () => {
    const recorded = await openThresholds();
    const user = userEvent.setup();

    await user.clear(screen.getByLabelText('Органический трафик'));

    expect(await screen.findByText('Впишите число')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
    await pastDebounce();
    expect(previews(recorded).some((body) => body.min_org_traffic === 0)).toBe(false);
  });

  it('поправили — отказ ушёл, последствия посчитаны, сохранение открыто', async () => {
    const recorded = await openThresholds();
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    await user.type(dr, '95');
    await screen.findByText('Допустимо от 0 до 90');
    await user.type(dr, '{backspace}{backspace}45');

    await waitFor(() => expect(screen.queryByText('Допустимо от 0 до 90')).toBeNull());
    expect(await screen.findByText('13')).toBeInTheDocument();
    expect(previews(recorded).at(-1)?.min_dr).toBe(45);
    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeEnabled();
  });

  it('границы — у значка подсказки, числами по-русски', async () => {
    await openThresholds();
    const user = userEvent.setup();

    await user.hover(
      screen.getByRole('button', { name: 'Допустимые значения: Органический трафик' }),
    );

    expect(
      await screen.findByText(/Допустимо: целое число от 0 до 10\s000\s000/),
    ).toBeInTheDocument();
  });

  it('границы берутся из ответа сервера, а не живут в экране', async () => {
    await openThresholds({}, { ...VIEW, limits: { ...LIMITS, min_dr: { min: 10, max: 50 } } });
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    await user.type(dr, '60');

    expect(await screen.findByText('Допустимо от 10 до 50')).toBeInTheDocument();
  });

  it('имя поля — его подпись, без слов значка подсказки', async () => {
    await openThresholds();

    // Кнопка подсказки стоит в подписи; подпись поэтому не `<label>`, и имя
    // полю дано словами подписи — иначе программа чтения с экрана читала бы
    // «DR не ниже Допустимые значения: DR не ниже».
    expect(screen.getByRole('textbox', { name: 'DR не ниже' })).toBeInTheDocument();
  });
});

describe('экран в покое', () => {
  it('сам себя не перерисовывает', async () => {
    // Черновик собирался заново на каждой отрисовке, а пауза набора
    // перезапускает таймер на каждый новый объект: экран перерисовывал
    // сам себя каждые 400 мс, пока открыт (найдено чтением кода 28.09.2026).
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/settings/thresholds': { body: VIEW },
    });
    let renders = 0;
    renderWith(
      <Profiler id="пороги" onRender={() => (renders += 1)}>
        <AppRoutes />
      </Profiler>,
      '/settings',
    );
    await screen.findByRole('heading', { name: 'Пороги отбора' });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });

    const settled = renders;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1300));
    });

    expect(renders).toBe(settled);
  });
});
