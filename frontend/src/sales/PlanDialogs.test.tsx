/**
 * Окна кнопок гипотезы плана: «Собрать ещё», «Качество сайтов», «Добавить ключи». Ответы сервера —
 * записанные (`serve()`); имена, числа и ключи выдуманы.
 *
 * Проверяется: на сервер уходит только поправленное (качество, потолок гипотезы — числом, а в поле
 * точка и запятая не стираются на ходу) или то, что вписано (цель, ключи текстом и книгой); ничего
 * не поправили — окно закрывается без запроса; удалось — уведомление
 * словами и закрытое окно; отказ сервера — плашкой словами сервера, набранное на месте. Каркас трёх
 * окон: заголовок короткий, имя гипотезы — строкой под ним; отказ — плашкой без заголовка над
 * прокруткой, кнопки — под ней (окно одной прокрутки); фокус при открытии — на первом поле.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import type { PlanRow } from './planApi';
import { addedWords, PlanKeys } from './PlanKeys';
import { PlanMore } from './PlanMore';
import { PlanQuality } from './PlanQuality';

type Route = Answer | ((call: Call) => Answer);

const ROW: PlanRow = {
  id: 1,
  hypothesis_id: 11,
  name: 'Логистика — пример',
  position: 1,
  sheet: 'Пилот 1',
  markets: ['kz'],
  language: 'ru',
  target: 5000,
  max_pages: 2,
  cap_usd: 50,
  stage: 'collecting',
  found: 1240,
  spent_usd: 12.4,
  keywords: 3,
  bought: 3,
  wave_page: 2,
  wave_bought: 1,
  reserved_usd: 0,
  problems: [],
};

const CARD: HypothesisCard = {
  id: 11,
  name: 'Логистика — пример',
  description: 'Транспортные компании со своим сайтом.',
  created_at: '2026-11-16T09:00:00+00:00',
  leads: { new: 0, ready: 0, rejected: 0 },
  total: 0,
};

function setUp(routes: Record<string, Route>): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  return serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/plans': { body: { plans: [] } },
    'GET /api/sales/countries': { body: { codes: ['KZ', 'RU', 'UZ'] } },
    ...routes,
  });
}

function sent(recorded: Recorded, path: string): Call[] {
  return recorded.calls.filter((call) => call.path === path && call.method !== 'GET');
}

/** Форма, ушедшая на сервер последней, — из вызова `fetch`. */
function lastForm(): FormData {
  const body = vi.mocked(globalThis.fetch).mock.calls.at(-1)?.[1]?.body;
  if (!(body instanceof FormData)) throw new Error('последний запрос — не форма');
  return body;
}

/** Окна по главной кнопке: что открывает каркас и куда уходит отказ. */
const DIALOGS: { title: string; render: () => ReactElement; route: string; fill?: string }[] = [
  {
    title: 'Собрать ещё',
    render: () => <PlanMore row={ROW} onClose={vi.fn()} />,
    route: 'POST /api/sales/plan-hypotheses/1/more',
    fill: 'Сколько сайтов ещё найти',
  },
  {
    title: 'Качество сайтов',
    render: () => <PlanQuality row={ROW} card={CARD} onClose={vi.fn()} />,
    route: 'PATCH /api/sales/plan-hypotheses/1/quality',
  },
  {
    title: 'Добавить ключи',
    render: () => <PlanKeys row={ROW} onClose={vi.fn()} />,
    route: 'POST /api/sales/plan-hypotheses/1/keywords',
    fill: 'Ключи',
  },
];

describe('каркас окна', () => {
  it.each(DIALOGS)(
    '«$title»: заголовок короткий, имя гипотезы — строкой под ним',
    ({ title, render }) => {
      setUp({});
      renderWith(render());

      const dialog = screen.getByRole('dialog');
      expect(dialog).toHaveAccessibleName(`${title} «${ROW.name}»`);
      const heading = within(dialog).getByRole('heading', { level: 2 });
      const name = within(heading).getByText(`«${ROW.name}»`);
      expect(heading.firstChild?.textContent).toBe(title);
      expect(name).not.toBe(heading);
    },
  );

  it.each(DIALOGS)(
    '«$title»: отказ — плашкой без заголовка над прокруткой, кнопки — под ней',
    async ({ render, route, fill }) => {
      setUp({ [route]: { status: 409, body: { detail: 'повторите через минуту' } } });
      renderWith(render());
      const user = userEvent.setup();

      if (fill !== undefined) await user.type(screen.getByLabelText(fill), '7');
      else await user.click(screen.getByRole('radio', { name: 'топ-50' }));
      const submit = screen
        .getAllByRole('button')
        .find((button) => button.getAttribute('type') === 'submit');
      if (submit === undefined) throw new Error('главной кнопки нет');
      await user.click(submit);

      const alert = await screen.findByRole('alert');
      expect(alert).toHaveTextContent(/: повторите через минуту$/);
      expect(alert.querySelector('.mantine-Alert-title')).toBeNull();
      const middle = screen.getByRole('dialog').querySelector('.oneScrollArea');
      expect(middle).not.toBeNull();
      expect(middle).not.toContainElement(alert);
      expect(middle).not.toContainElement(submit);
      expect(middle).not.toContainElement(screen.getByRole('button', { name: 'Отмена' }));
    },
  );

  it('фокус при открытии — на первом поле: у «Качества сайтов» — выбранная глубина', async () => {
    setUp({});
    renderWith(<PlanQuality row={ROW} card={CARD} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole('radio', { name: 'топ-20' })).toHaveFocus());
  });
});

describe('«Собрать ещё»', () => {
  it('цель уходит числом, окно закрывается; сказано, какой станет цель', async () => {
    const onClose = vi.fn();
    const recorded = setUp({
      'POST /api/sales/plan-hypotheses/1/more': { body: { ...ROW, target: 5500 } },
    });
    renderWith(<PlanMore row={ROW} onClose={onClose} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Сколько сайтов ещё найти'), '500');
    expect(screen.getByText(/Цель станет 5 500/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Собрать ещё' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sent(recorded, '/api/sales/plan-hypotheses/1/more').map((call) => call.body)).toEqual([
      { n: 500 },
    ]);
  });

  it('отказ сервера — плашкой словами, число на месте', async () => {
    const words = 'цель гипотезы «Логистика — пример» — от 1 до 1 000 000';
    setUp({
      'POST /api/sales/plan-hypotheses/1/more': { status: 400, body: { detail: words } },
    });
    renderWith(<PlanMore row={ROW} onClose={vi.fn()} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Сколько сайтов ещё найти'), '7');
    await user.click(screen.getByRole('button', { name: 'Собрать ещё' }));

    expect(await screen.findByText('Цель не поправлена')).toBeInTheDocument();
    expect(screen.getByText(/от 1 до 1 000 000/)).toBeInTheDocument();
    expect(screen.getByLabelText('Сколько сайтов ещё найти')).toHaveValue('7');
  });
});

describe('«Качество сайтов»', () => {
  it('уходит только поправленное: глубина и цель', async () => {
    const onClose = vi.fn();
    const recorded = setUp({
      'PATCH /api/sales/plan-hypotheses/1/quality': { body: { ...ROW, max_pages: 3 } },
    });
    renderWith(<PlanQuality row={ROW} card={CARD} onClose={onClose} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'топ-30' }));
    const target = screen.getByLabelText('Найти сайтов');
    await user.clear(target);
    await user.type(target, '6000');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sent(recorded, '/api/sales/plan-hypotheses/1/quality').map((call) => call.body)).toEqual(
      [{ max_pages: 3, target: 6000 }],
    );
  });

  it('потолок гипотезы — в поле как набрано, с точкой и запятой; уходит числом', async () => {
    // Урок 19.11.2026: поле, перерисованное из числа, стирало точку на ходу — «1.0» становилось
    // пустым полем, «0,5» — «5». Запятую поле пишет точкой.
    const onClose = vi.fn();
    const recorded = setUp({
      'PATCH /api/sales/plan-hypotheses/1/quality': { body: { ...ROW, cap_usd: 75.5 } },
    });
    renderWith(<PlanQuality row={ROW} card={CARD} onClose={onClose} />);
    const user = userEvent.setup();
    const cap = screen.getByLabelText('Потолок гипотезы, $');
    expect(cap).toHaveValue('50');

    await user.clear(cap);
    await user.type(cap, '1.0');
    expect(cap).toHaveValue('1.0');
    await user.clear(cap);
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    await user.type(cap, '75,50');
    expect(cap).toHaveValue('75.50');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(sent(recorded, '/api/sales/plan-hypotheses/1/quality').map((call) => call.body)).toEqual(
      [{ cap_usd: 75.5 }],
    );
  });

  it('потолок ниже израсходованного — отказ сервера плашкой, набранное на месте', async () => {
    const words =
      'потолок гипотезы «Логистика — пример» не может быть ниже израсходованного: израсходовано ' +
      '12,40 $ — задайте от 12,40 $';
    setUp({
      'PATCH /api/sales/plan-hypotheses/1/quality': { status: 409, body: { detail: words } },
    });
    renderWith(<PlanQuality row={ROW} card={CARD} onClose={vi.fn()} />);
    const user = userEvent.setup();
    const cap = screen.getByLabelText('Потолок гипотезы, $');

    await user.clear(cap);
    await user.type(cap, '10.5');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('Качество не сохранено')).toBeInTheDocument();
    expect(screen.getByText(/не может быть ниже израсходованного/)).toBeInTheDocument();
    expect(cap).toHaveValue('10.5');
  });

  it('ничего не поправили — окно закрыто, запроса нет', async () => {
    const onClose = vi.fn();
    const recorded = setUp({});
    renderWith(<PlanQuality row={ROW} card={CARD} onClose={onClose} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(onClose).toHaveBeenCalled();
    expect(sent(recorded, '/api/sales/plan-hypotheses/1/quality')).toHaveLength(0);
  });

  it('отказ сервера — плашкой словами', async () => {
    const words = 'план сейчас меняет проход конвейера или другая кнопка — повторите через минуту';
    setUp({
      'PATCH /api/sales/plan-hypotheses/1/quality': { status: 409, body: { detail: words } },
    });
    renderWith(<PlanQuality row={ROW} card={CARD} onClose={vi.fn()} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'топ-50' }));
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('Качество не сохранено')).toBeInTheDocument();
    expect(screen.getByText(/повторите через минуту/)).toBeInTheDocument();
  });
});

describe('«Добавить ключи»', () => {
  it('итог словами: новые, уже были, отсеяно', () => {
    expect(addedWords({ hypothesis: ROW, added: 1, known: 2, rejected: 0 })).toBe(
      'Добавлено 1 ключ · уже были 2',
    );
    expect(addedWords({ hypothesis: ROW, added: 5, known: 0, rejected: 3 })).toBe(
      'Добавлено 5 ключей · отсеяно 3',
    );
  });

  it('текстом — ключи уходят полем формы, окно закрывается', async () => {
    const onClose = vi.fn();
    setUp({
      'POST /api/sales/plan-hypotheses/1/keywords': {
        body: { hypothesis: ROW, added: 2, known: 0, rejected: 0 },
      },
    });
    renderWith(<PlanKeys row={ROW} onClose={onClose} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключи'), 'Fußpflege Praxis{enter}家具 批发');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(lastForm().get('text')).toBe('Fußpflege Praxis\n家具 批发');
  });

  it('книгой — файл уходит полем формы; отказ сервера — плашкой словами', async () => {
    const words = 'в книге ключи.xlsx листов: 2 — ключи берутся из книги одним листом';
    setUp({
      'POST /api/sales/plan-hypotheses/1/keywords': { status: 400, body: { detail: words } },
    });
    renderWith(<PlanKeys row={ROW} onClose={vi.fn()} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Книгой .xlsx' }));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    if (input === null) throw new Error('поля книги нет');
    await user.upload(input, new File(['PK'], 'ключи.xlsx'));
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(await screen.findByText('Ключи не добавлены')).toBeInTheDocument();
    expect(screen.getByText(/одним листом/)).toBeInTheDocument();
    expect((lastForm().get('file') as File).name).toBe('ключи.xlsx');
  });
});
