/**
 * Вкладка «Цепочка писем»: цепочки по языкам, шаги, окно шага, письмо глазами адресата.
 * Тексты выдуманы («Hello {{name}}, test body»); ответы сервера — записанные (`serve()`),
 * незаписанный запрос роняет тест.
 *
 * Проверяется то, ради чего вкладка: «цепочка не задана» видно словами, что уходит на
 * сервер (тело шага целиком, тема только у первого письма, набор гипотезы), что экран
 * перечитывает после записи, и что отказ сервера виден словами, а не пропадает. Без права
 * отправки писем шаги и письмо глазами адресата видны, а записать нечем — сказано строкой.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type {
  ChainPreviewView,
  ChainState,
  ChainStepCard,
  ChainView,
  HypothesesView,
} from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };
const ALL_MISSING = ['первого письма', 'первой добивки', 'второй добивки'];

const FIRST_EN: ChainStepCard = {
  id: 11,
  hypothesis_id: null,
  step: 1,
  language: 'en',
  subject: 'Test for {{company}}',
  body: '[greeting] rewrite\nHello {{name}},\n\n[offer] fixed\nTest offer body.',
  zones: [
    { name: 'greeting', kind: 'rewrite', text: 'Hello {{name}},' },
    { name: 'offer', kind: 'fixed', text: 'Test offer body.' },
  ],
  active: true,
  updated_by: 'seller@ours.example.test',
  created_at: '2026-10-05T09:00:00+00:00',
  updated_at: '2026-10-05T10:00:00+00:00',
};

const SECOND_EN: ChainStepCard = {
  ...FIRST_EN,
  id: 12,
  step: 2,
  subject: null,
  body: '[reminder] fixed\nJust a test reminder.',
  zones: [{ name: 'reminder', kind: 'fixed', text: 'Just a test reminder.' }],
};

const THIRD_EN: ChainStepCard = { ...SECOND_EN, id: 13, step: 3, active: false };

function state(language: string, missing: string[], source: ChainState['source'] = 'common') {
  return { language, source, missing, version: `chain-${language}0000000000` };
}

const EMPTY: ChainView = {
  hypothesis_id: null,
  rows: [],
  chains: [state('ru', ALL_MISSING), state('en', ALL_MISSING)],
  steps: [1, 2, 3],
  languages: ['ru', 'en'],
  placeholders: ['name', 'company', 'site'],
  limits: { subject: 255, body: 10000 },
};

const FILLED: ChainView = {
  ...EMPTY,
  rows: [FIRST_EN, SECOND_EN, THIRD_EN],
  chains: [state('ru', ALL_MISSING), state('en', ['второй добивки'])],
};

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 5,
      name: 'тестовая гипотеза',
      description: null,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 0, ready: 0, rejected: 0 },
      total: 0,
    },
  ],
  total: 1,
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openChain(
  routes: Routes = {},
  view: ChainView = FILLED,
  who: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/chain': { body: view },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=chain');
  await screen.findByRole('heading', { name: 'Английский' }, SCREEN_WAIT);
  return recorded;
}

function cardOf(language: string): HTMLElement {
  return screen.getByRole('region', { name: language });
}

function stepOf(language: string, title: string): HTMLElement {
  const row = within(cardOf(language)).getByText(title).closest('.chainStep');
  if (row === null) throw new Error(`шага «${title}» нет`);
  return row as HTMLElement;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

async function pickSet(user: ReturnType<typeof userEvent.setup>, option: string) {
  const field = screen.getByRole('textbox', { name: 'Набор' });
  await user.click(field);
  const listId = field.getAttribute('aria-controls');
  if (listId === null) throw new Error('у поля нет списка');
  const list = await waitFor(() => {
    const found = document.getElementById(listId);
    if (found === null) throw new Error('список ещё не открыт');
    return found;
  }, SCREEN_WAIT);
  await user.click(within(list).getByRole('option', { name: option, hidden: true }));
}

describe('цепочка писем: языки и шаги', () => {
  it('вступление — одной строкой, где живут тексты и чья цепочка — в «i» (аудит 09.10.2026)', async () => {
    await openChain();
    const user = userEvent.setup();

    expect(screen.getByText(/^Первое письмо и две добивки/)).toBeInTheDocument();
    expect(screen.queryByText(/Своя цепочка гипотезы на языке заменяет общую/)).toBeNull();

    await user.hover(
      screen.getByRole('button', { name: 'Где живут тексты и чья цепочка действует' }),
    );

    expect(
      await screen.findByText(/Своя цепочка гипотезы на языке заменяет общую/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('пустой набор — у каждого языка «цепочка не задана» словами и дорога к загрузке', async () => {
    await openChain({}, EMPTY);

    for (const language of ['Русский', 'Английский']) {
      const card = within(cardOf(language));
      expect(card.getByText('цепочка не задана')).toBeInTheDocument();
      expect(
        card.getByText(
          'Нет первого письма, первой добивки, второй добивки — письма продаж на этом языке не соберутся.',
        ),
      ).toBeInTheDocument();
      expect(card.getAllByText('не задан')).toHaveLength(3);
      expect(card.getAllByRole('button', { name: 'Задать' })).toHaveLength(3);
    }
    expect(
      screen.getByText(/Задайте шаги здесь; набор целиком из файла загружает администратор/),
    ).toBeInTheDocument();
  });

  it('шаг называет тему, начало текста и кто правил; неполная цепочка — чего нет', async () => {
    await openChain();

    const first = within(stepOf('Английский', '1. Первое письмо'));
    expect(first.getByText('задан')).toBeInTheDocument();
    expect(first.getByText('Тема: Test for {{company}}')).toBeInTheDocument();
    expect(first.getByText('Hello {{name}}, · Test offer body.')).toBeInTheDocument();
    expect(first.getByText(/seller@ours\.example\.test/)).toBeInTheDocument();
    expect(within(stepOf('Английский', '3. Вторая добивка')).getByText('выключен')).toBeVisible();

    const english = within(cardOf('Английский'));
    expect(english.getByText('цепочка неполна')).toBeInTheDocument();
    expect(
      english.getByText('Нет второй добивки — письма продаж на этом языке не соберутся.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/загружает администратор/)).not.toBeInTheDocument();
  });

  it('полная цепочка — с версией: по ней сборка и калибровка узнают текст', async () => {
    await openChain({}, { ...FILLED, chains: [state('ru', []), state('en', [])] });

    const english = within(cardOf('Английский'));
    expect(english.getByText('цепочка полна')).toBeInTheDocument();
    expect(english.getByText('Версия chain-en0000000000.')).toBeInTheDocument();
  });

  it('набор гипотезы — свой запрос; без своих шагов сказано, что действует общая цепочка', async () => {
    const own: ChainView = {
      ...EMPTY,
      hypothesis_id: 5,
      chains: [state('ru', ALL_MISSING), state('en', [], 'common')],
    };
    const recorded = await openChain({ 'GET /api/sales/chain?hypothesis=5': { body: own } });
    const user = userEvent.setup();

    await pickSet(user, 'тестовая гипотеза');

    await waitFor(
      () => expect(calls(recorded, 'GET', '/api/sales/chain?hypothesis=5')).toHaveLength(1),
      SCREEN_WAIT,
    );
    const english = within(cardOf('Английский'));
    expect(
      await english.findByText(
        'Своих включённых шагов на этом языке нет — действует общая цепочка. Версия chain-en0000000000.',
      ),
    ).toBeInTheDocument();
    expect(english.getAllByText('не задан')).toHaveLength(3);
  });

  it('набор не загрузился — отказ сервера словами', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: HYPOTHESES },
      'GET /api/sales/kb': { body: KB },
      'GET /api/sales/chain': { status: 503, body: { detail: 'База не ответила — повторите' } },
    });
    renderWith(<AppRoutes />, '/sales?tab=chain');

    expect(
      await screen.findByText('Цепочка писем не загрузилась', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText('База не ответила — повторите')).toBeInTheDocument();
  });
});

describe('цепочка писем: окно шага', () => {
  it('правка первого письма уходит телом целиком, и набор перечитывается', async () => {
    const recorded = await openChain({
      'POST /api/sales/chain': { body: { ...FIRST_EN, subject: 'Another test subject' } },
    });
    const before = calls(recorded, 'GET', '/api/sales/chain').length;
    const user = userEvent.setup();

    await user.click(within(stepOf('Английский', '1. Первое письмо')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    const subject = dialog.getByRole('textbox', { name: 'Тема' });
    await user.clear(subject);
    await user.type(subject, 'Another test subject');
    await user.click(dialog.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(calls(recorded, 'POST', '/api/sales/chain')).toHaveLength(1));
    expect(calls(recorded, 'POST', '/api/sales/chain')[0]?.body).toEqual({
      step: 1,
      language: 'en',
      subject: 'Another test subject',
      body: FIRST_EN.body,
      hypothesis_id: null,
      active: true,
    });
    await waitFor(() =>
      expect(calls(recorded, 'GET', '/api/sales/chain').length).toBeGreaterThan(before),
    );
    expect(await screen.findByText('Сохранено: Первое письмо · Английский')).toBeInTheDocument();
  });

  it('новая добивка гипотезы: поля темы нет, тема уходит null, набор — гипотезы', async () => {
    const own: ChainView = { ...EMPTY, hypothesis_id: 5 };
    const recorded = await openChain({
      'GET /api/sales/chain?hypothesis=5': { body: own },
      'POST /api/sales/chain': { body: { ...SECOND_EN, hypothesis_id: 5, language: 'ru' } },
    });
    const user = userEvent.setup();
    await pickSet(user, 'тестовая гипотеза');
    await waitFor(
      () => expect(calls(recorded, 'GET', '/api/sales/chain?hypothesis=5')).toHaveLength(1),
      SCREEN_WAIT,
    );
    await screen.findByRole('region', { name: 'Русский' }, SCREEN_WAIT);

    await user.click(within(stepOf('Русский', '2. Первая добивка')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    expect(dialog.queryByRole('textbox', { name: 'Тема' })).not.toBeInTheDocument();
    await user.click(dialog.getByRole('textbox', { name: 'Текст письма' }));
    await user.paste('[reminder] fixed\nJust a test reminder, {{name}}.');
    await user.click(dialog.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(calls(recorded, 'POST', '/api/sales/chain')).toHaveLength(1));
    expect(calls(recorded, 'POST', '/api/sales/chain')[0]?.body).toEqual({
      step: 2,
      language: 'ru',
      subject: null,
      body: '[reminder] fixed\nJust a test reminder, {{name}}.',
      hypothesis_id: 5,
      active: true,
    });
  });

  it('отказ сервера при записи — словами над формой, введённое остаётся', async () => {
    const refusal = 'тема «Re: test» начинается с «Re:» — переписки ещё нет';
    await openChain({ 'POST /api/sales/chain': { status: 400, body: { detail: refusal } } });
    const user = userEvent.setup();

    await user.click(within(stepOf('Английский', '1. Первое письмо')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    const subject = dialog.getByRole('textbox', { name: 'Тема' });
    await user.clear(subject);
    await user.type(subject, 'Re: test');
    await user.click(dialog.getByRole('button', { name: 'Сохранить' }));

    const alert = await dialog.findByText('Не сохранили', {}, SCREEN_WAIT);
    expect(within(alert.closest('[role="alert"]') as HTMLElement).getByText(refusal)).toBeVisible();
    expect(subject).toHaveValue('Re: test');
  });

  it('пустая тема первого письма — отказ под полем до нажатия, «Сохранить» выключена', async () => {
    await openChain();
    const user = userEvent.setup();

    await user.click(within(stepOf('Английский', '1. Первое письмо')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    await user.clear(dialog.getByRole('textbox', { name: 'Тема' }));

    expect(dialog.getByText('Впишите тему — у первого письма она обязательна')).toBeVisible();
    expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Показать письмо' })).toBeDisabled();
  });
});

describe('цепочка писем: письмо глазами адресата', () => {
  const LETTER: ChainPreviewView = {
    subject: 'Test for Example Company',
    zones: [
      { name: 'greeting', kind: 'rewrite', text: 'Hello Alex Example,' },
      { name: 'offer', kind: 'fixed', text: 'Test offer body.' },
    ],
    values: { name: 'Alex Example', company: 'Example Company', site: 'example.com' },
    sender_name: 'Iva Testova',
    signature: 'Iva Testova\nTest lead',
    address: null,
    missing: ['не задан физический адрес'],
  };

  it('«Показать письмо» шлёт черновик и показывает тему, зоны, подпись и чего нет', async () => {
    const recorded = await openChain({ 'POST /api/sales/chain/preview': { body: LETTER } });
    const user = userEvent.setup();

    await user.click(within(stepOf('Английский', '1. Первое письмо')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    await user.click(dialog.getByRole('button', { name: 'Показать письмо' }));

    const letter = within(await dialog.findByLabelText('Письмо глазами адресата'));
    expect(calls(recorded, 'POST', '/api/sales/chain/preview')[0]?.body).toEqual({
      step: 1,
      language: 'en',
      subject: FIRST_EN.subject,
      body: FIRST_EN.body,
    });
    expect(letter.getByText('Test for Example Company')).toBeInTheDocument();
    expect(letter.getByText('Hello Alex Example,')).toBeInTheDocument();
    expect(letter.getByText('переписывает модель')).toBeInTheDocument();
    expect(letter.getByText('уходит как есть')).toBeInTheDocument();
    expect(letter.getByText(/Test lead/)).toBeInTheDocument();
    expect(
      letter.getByText('Физический адрес не задан — заполните вкладку «Отправитель»'),
    ).toBeInTheDocument();
    expect(letter.getByText('Отправка продаж не готова')).toBeInTheDocument();
    expect(
      letter.getByText(
        'Для примера подставлено: имя адресата — Alex Example; компания — Example Company; сайт компании — example.com.',
      ),
    ).toBeInTheDocument();
  });

  it('правка после показа убирает показанное письмо — оно было бы по прежнему тексту', async () => {
    await openChain({ 'POST /api/sales/chain/preview': { body: LETTER } });
    const user = userEvent.setup();
    await user.click(within(stepOf('Английский', '1. Первое письмо')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    await user.click(dialog.getByRole('button', { name: 'Показать письмо' }));
    await dialog.findByLabelText('Письмо глазами адресата');

    await user.type(dialog.getByRole('textbox', { name: 'Тема' }), ' more');

    expect(dialog.queryByLabelText('Письмо глазами адресата')).not.toBeInTheDocument();
  });

  it('письмо не собралось — отказ сервера словами', async () => {
    const refusal = 'подстановки {{nam}} нет; есть только {{name}}, {{company}}, {{site}}';
    await openChain({
      'POST /api/sales/chain/preview': { status: 400, body: { detail: refusal } },
    });
    const user = userEvent.setup();
    await user.click(within(stepOf('Английский', '1. Первое письмо')).getByRole('button'));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));

    await user.click(dialog.getByRole('button', { name: 'Показать письмо' }));

    expect(await dialog.findByText('Письмо не собралось', {}, SCREEN_WAIT)).toBeVisible();
    expect(dialog.getByText(refusal)).toBeVisible();
  });
});

describe('цепочка писем: без права на отправку', () => {
  const LINE = 'Правит цепочку тот, у кого есть право отправки писем. Смотреть можно всем.';
  const SHOWN: ChainPreviewView = {
    subject: 'Test for Example Company',
    zones: [{ name: 'greeting', kind: 'rewrite', text: 'Hello Alex Example,' }],
    values: { name: 'Alex Example', company: 'Example Company', site: 'example.com' },
    sender_name: null,
    signature: null,
    address: null,
    missing: [],
  };

  it('шаги видны, заданный открывают смотреть, задать новый нельзя — строка говорит почему', async () => {
    await openChain({}, FILLED, OPERATOR);

    expect(screen.getByText(LINE)).toBeInTheDocument();
    const first = within(stepOf('Английский', '1. Первое письмо'));
    expect(first.getByText('Тема: Test for {{company}}')).toBeInTheDocument();
    expect(first.getByRole('button', { name: 'Открыть' })).toBeEnabled();
    expect(
      within(stepOf('Русский', '1. Первое письмо')).getByRole('button', { name: 'Задать' }),
    ).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Править' })).not.toBeInTheDocument();
  });

  it('окно шага: поля и «Сохранить» закрыты, письмо глазами адресата — как было', async () => {
    const recorded = await openChain(
      { 'POST /api/sales/chain/preview': { body: SHOWN } },
      FILLED,
      OPERATOR,
    );
    const user = userEvent.setup();

    await user.click(
      within(stepOf('Английский', '1. Первое письмо')).getByRole('button', { name: 'Открыть' }),
    );
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    expect(dialog.getByRole('textbox', { name: 'Тема' })).toHaveValue(FIRST_EN.subject);
    expect(dialog.getByRole('textbox', { name: 'Тема' })).toBeDisabled();
    expect(dialog.getByRole('textbox', { name: 'Текст письма' })).toBeDisabled();
    expect(dialog.getByRole('switch', { name: 'Шаг включён — входит в цепочку' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(dialog.getByText(LINE)).toBeInTheDocument();

    await user.click(dialog.getByRole('button', { name: 'Показать письмо' }));

    expect(await dialog.findByLabelText('Письмо глазами адресата', {}, SCREEN_WAIT)).toBeVisible();
    expect(calls(recorded, 'POST', '/api/sales/chain/preview')).toHaveLength(1);
    expect(calls(recorded, 'POST', '/api/sales/chain')).toEqual([]);
  });

  it('с правом отправки строки нет', async () => {
    await openChain();

    expect(screen.queryByText(LINE)).not.toBeInTheDocument();
  });
});
