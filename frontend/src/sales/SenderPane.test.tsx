/**
 * Вкладка «Отправитель»: готовность к отправке словами сервера, форма целиком,
 * границы сервера до нажатия, отказ словами. Без права отправки писем форма видна,
 * а записать нечем — сказано строкой. Адреса и подписи выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { SENDER_FIELDS } from '../api/salesLabels';
import type { SenderLanguageCard } from '../api/salesSenderTypes';
import type { SenderView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };

const LIMITS: SenderView['limits'] = {
  sender_name: 128,
  sender_position: 128,
  signature: 1000,
  website: 255,
  telegram: 255,
  physical_address: 500,
  call_link: 512,
};

const NO_SET = {
  sender_name: null,
  sender_position: null,
  signature: null,
  physical_address: null,
};
/** Наборы языков писем — словами сервера, в порядке языков цепочек. */
const RU: SenderLanguageCard = { language: 'ru', title: 'Письма на русском', ...NO_SET };
const EN: SenderLanguageCard = { language: 'en', title: 'Письма на английском', ...NO_SET };
const LANGUAGES = [RU, EN];

const EMPTY: SenderView = {
  sender_name: null,
  sender_position: null,
  signature: null,
  website: null,
  telegram: null,
  physical_address: null,
  call_link: null,
  languages: LANGUAGES,
  updated_by: null,
  updated_at: null,
  missing: ['не задан физический адрес', 'не задана подпись', 'не задано имя отправителя'],
  limits: LIMITS,
  // Образец подстановки — сервера (`chain_text.UNFILLED`), как его отдаёт `GET /sales/sender`.
  unfilled: '\\{\\{[^{}]*\\}\\}|«[^«»]*НЕ ЗАДАН[^«»]*»',
};

async function openSender(
  routes: Record<string, Answer | ((call: Call) => Answer)> = {},
  sender: SenderView = EMPTY,
  who: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } },
    'GET /api/sales/kb': {
      body: {
        rows: [],
        total: 0,
        active: 0,
        version: 'kb-000000000000',
        kinds: ['brief'],
        limits: { title: 255, text: 20000, tag: 64, tags: 20 },
      },
    },
    'GET /api/sales/sender': { body: sender },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=sender');
  await screen.findByText(/Отправка продаж/, {}, SCREEN_WAIT);
  return recorded;
}

function field(name: string): HTMLElement {
  return screen.getByRole('textbox', { name });
}

describe('отправитель', () => {
  it('поля по значению: однострочные не шире 22rem; «Сохранить» слева, как у порогов и агента (аудит 09.10.2026)', async () => {
    await openSender();

    // Имя, должность, ссылки — одна строка, а поле было на половину панели.
    for (const name of ['Имя отправителя', 'Сайт', 'Telegram для лидов']) {
      expect(field(name).closest('.mantine-InputWrapper-root')?.getAttribute('style')).toMatch(
        /max-width: 22rem/,
      );
    }
    // Подпись и адрес — в несколько строк: им ширина панели и нужна.
    expect(
      field('Подпись').closest('.mantine-InputWrapper-root')?.getAttribute('style') ?? '',
    ).not.toMatch(/max-width/);
    const save = screen.getByRole('button', { name: 'Сохранить' });
    const who = screen.getByText('Ещё не заполнялся.');
    expect(save.compareDocumentPosition(who) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(save.closest('.mantine-Group-root')?.getAttribute('style') ?? '').not.toMatch(
      /space-between/,
    );
  });

  it('пустой: чего не хватает для отправки — словами сервера, сохранять нечего', async () => {
    await openSender();

    const warning = screen.getByText('Отправка продаж не готова').closest('[role="alert"]');
    expect(warning).toHaveTextContent(
      'не задан физический адрес; не задана подпись; не задано имя отправителя — ' +
        'без этого письмо продаж не уходит.',
    );
    expect(field('Физический адрес')).toHaveValue('');
    expect(screen.getByText('Ещё не заполнялся.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('сохраняется целиком: пустое уходит null, готовность — из ответа сервера', async () => {
    const saved: SenderView = {
      ...EMPTY,
      sender_name: 'Ива Тестова',
      signature: 'Ива Тестова',
      physical_address: 'Выдуманная ул., 7',
      updated_by: 'seller@ours.example.test',
      updated_at: '2026-10-05T10:00:00+00:00',
      missing: [],
    };
    const recorded = await openSender({ 'POST /api/sales/sender': { body: saved } });
    const user = userEvent.setup();

    await user.type(field('Имя отправителя'), 'Ива Тестова');
    await user.type(field('Подпись'), 'Ива Тестова');
    await user.type(field('Физический адрес'), 'Выдуманная ул., 7');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(recorded.calls.filter((c) => c.method === 'POST')).toHaveLength(1));
    expect(recorded.calls.find((c) => c.method === 'POST')?.body).toEqual({
      sender_name: 'Ива Тестова',
      sender_position: null,
      signature: 'Ива Тестова',
      physical_address: 'Выдуманная ул., 7',
      website: null,
      telegram: null,
      call_link: null,
      // Наборы языков не трогали — уходят пустыми: в письмах на этих языках общие поля.
      languages: { ru: NO_SET, en: NO_SET },
    });
    const ready = (await screen.findByText('Отправка продаж готова', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    // Поля правила называет только сервер: «готова» их не перечисляет.
    expect(ready).toHaveTextContent('Всё, без чего письмо продаж не уходит, задано.');
    expect(screen.getByText(/Правил seller@ours\.example\.test/)).toBeInTheDocument();
    expect(field('Подпись')).toHaveValue('Ива Тестова');
  });

  it('отказ сервера — словами над формой, набранное стоит', async () => {
    await openSender({
      'POST /api/sales/sender': {
        status: 400,
        body: { detail: 'сайт: «studio.example.test» — не ссылка, ждём https://…' },
      },
    });
    const user = userEvent.setup();

    await user.type(field('Сайт'), 'studio.example.test');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const refusal = await screen.findByText('Не сохранили', {}, SCREEN_WAIT);
    expect(
      within(refusal.closest('[role="alert"]') as HTMLElement).getByText(/не ссылка/),
    ).toBeInTheDocument();
    expect(field('Сайт')).toHaveValue('studio.example.test');
  });

  it('длиннее границы сервера — под полем до нажатия, на сервер не уходит', async () => {
    const recorded = await openSender({}, { ...EMPTY, limits: { ...LIMITS, sender_name: 5 } });
    const user = userEvent.setup();

    await user.type(field('Имя отправителя'), 'Ива Тестова');

    expect(screen.getByText('Длиннее 5 знаков: сейчас 11')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(recorded.calls.filter((c) => c.method === 'POST')).toEqual([]);
  });
});

describe('отправитель: набор на язык письма', () => {
  const HINT = 'Пусто — в письмах на этом языке будет общее поле';
  const MARK = 'не заполнено — общее';
  const UNFILLED =
    'подстановки здесь не работают: поле уходит в письмо как есть, напишите текст целиком';

  function group(title: string): HTMLElement {
    return screen.getByRole('group', { name: title });
  }

  async function unfold(title: string): Promise<void> {
    await userEvent.setup().click(screen.getByRole('button', { name: title }));
    await within(group(title)).findByText(HINT, {}, SCREEN_WAIT);
  }

  it('под общими полями — блок на каждый язык словами сервера; пустой свёрнут, рядом «не заполнено — общее»', async () => {
    await openSender();

    const russian = group('Письма на русском');
    const english = group('Письма на английском');
    // Блоки — под общими полями: после «Ссылки на созвон», русский — раньше английского.
    const call = field('Ссылка на созвон');
    expect(call.compareDocumentPosition(russian) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      russian.compareDocumentPosition(english) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // Пустой блок свёрнут: заголовок — кнопка, рядом пометка, полей и подсказки нет.
    for (const [block, title] of [
      [russian, 'Письма на русском'],
      [english, 'Письма на английском'],
    ] as const) {
      const toggle = within(block).getByRole('button', { name: title });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(within(block).getByText(MARK)).toBeVisible();
      expect(within(block).queryByRole('textbox')).not.toBeInTheDocument();
      expect(within(block).queryByText(HINT)).not.toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('раскрытый блок: подсказка и поля набора — те, что зависят от языка, без пояснений общих', async () => {
    await openSender();

    await unfold('Письма на английском');

    const english = group('Письма на английском');
    expect(within(english).getByRole('button', { name: 'Письма на английском' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(
      within(english)
        .getAllByRole('textbox')
        .map((input) => input.getAttribute('aria-label')),
    ).toEqual([
      'Имя отправителя — письма на английском',
      'Должность — письма на английском',
      'Подпись — письма на английском',
      'Физический адрес — письма на английском',
    ]);
    // Пояснения общих полей («обязателен», «без него не уходит») у набора не стоят.
    expect(within(english).queryByText(/обязателен/)).not.toBeInTheDocument();
    // Русский блок не раскрылся вместе с ним.
    expect(within(group('Письма на русском')).queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('блок, где задано хоть одно поле, открыт; набрал в пустом — пометка уходит', async () => {
    const filled: SenderView = {
      ...EMPTY,
      signature: 'Мира Тестова',
      languages: [RU, { ...EN, signature: 'Leo Sample' }],
    };
    await openSender({}, filled);

    expect(field('Подпись')).toHaveValue('Мира Тестова');
    expect(field('Подпись — письма на английском')).toHaveValue('Leo Sample');
    expect(within(group('Письма на английском')).queryByText(MARK)).not.toBeInTheDocument();
    expect(within(group('Письма на русском')).getByText(MARK)).toBeVisible();
    await unfold('Письма на русском');
    await userEvent.setup().type(field('Имя отправителя — письма на русском'), 'Мира');
    expect(within(group('Письма на русском')).queryByText(MARK)).not.toBeInTheDocument();
  });

  it('набор уходит телом вместе с общими полями: пустое — null, то есть общее поле', async () => {
    const recorded = await openSender({ 'POST /api/sales/sender': { body: EMPTY } });
    const user = userEvent.setup();

    await unfold('Письма на английском');
    await user.type(field('Подпись — письма на английском'), 'Leo Sample{enter}Sample Studio Ltd');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(recorded.calls.filter((c) => c.method === 'POST')).toHaveLength(1));
    const body = recorded.calls.find((c) => c.method === 'POST')?.body as Record<string, unknown>;
    expect(body.languages).toEqual({
      ru: NO_SET,
      en: { ...NO_SET, signature: 'Leo Sample\nSample Studio Ltd' },
    });
    expect(body.signature).toBeNull();
  });

  it('длиннее границы сервера — под полем набора до нажатия, на сервер не уходит', async () => {
    const recorded = await openSender({}, { ...EMPTY, limits: { ...LIMITS, sender_name: 5 } });

    await unfold('Письма на английском');
    await userEvent.setup().type(field('Имя отправителя — письма на английском'), 'Leo Sample');

    const english = group('Письма на английском');
    expect(within(english).getByText('Длиннее 5 знаков: сейчас 10')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(recorded.calls.filter((c) => c.method === 'POST')).toEqual([]);
  });

  it('подстановка — словами под полем до нажатия, в общем поле и в наборе; на сервер не уходит', async () => {
    const recorded = await openSender();
    const user = userEvent.setup();

    await user.click(field('Подпись'));
    await user.paste('Мира Тестова {{name}}');
    await unfold('Письма на английском');
    await user.click(field('Имя отправителя — письма на английском'));
    await user.paste('«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО»');

    expect(screen.getByText(`«{{name}}» — ${UNFILLED}`)).toBeVisible();
    expect(
      within(group('Письма на английском')).getByText(`«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО» — ${UNFILLED}`),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(recorded.calls.filter((c) => c.method === 'POST')).toEqual([]);
  });

  it('подстановка, сохранённая раньше правила, — видна под полем сразу; блок с ней не сворачивается', async () => {
    const stored: SenderView = {
      ...EMPTY,
      languages: [RU, { ...EN, physical_address: '{{address}}' }],
    };
    await openSender({}, stored);

    const english = group('Письма на английском');
    expect(within(english).getByText(`«{{address}}» — ${UNFILLED}`)).toBeVisible();
    await userEvent
      .setup()
      .click(within(english).getByRole('button', { name: 'Письма на английском' }));
    expect(field('Физический адрес — письма на английском')).toHaveValue('{{address}}');
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('отказ сервера по набору — словами над формой, набранное стоит', async () => {
    const words = 'Письма на английском — подпись: длиннее 1000 знаков (1001)';
    await openSender({ 'POST /api/sales/sender': { status: 400, body: { detail: words } } });
    const user = userEvent.setup();

    await unfold('Письма на английском');
    await user.type(field('Подпись — письма на английском'), 'Leo Sample');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const refusal = await screen.findByText('Не сохранили', {}, SCREEN_WAIT);
    expect(within(refusal.closest('[role="alert"]') as HTMLElement).getByText(words)).toBeVisible();
    expect(field('Подпись — письма на английском')).toHaveValue('Leo Sample');
  });
});

describe('отправитель: без права на отправку', () => {
  const LINE = 'Правит отправителя тот, у кого есть право отправки писем. Смотреть можно всем.';
  const FILLED: SenderView = {
    ...EMPTY,
    sender_name: 'Ива Тестова',
    signature: 'Ива Тестова',
    updated_by: 'seller@ours.example.test',
    updated_at: '2026-10-05T10:00:00+00:00',
    missing: ['не задан физический адрес'],
  };

  it('поля видны и закрыты, «Сохранить» закрыта, готовность — словами сервера, строка — почему', async () => {
    const recorded = await openSender({}, FILLED, OPERATOR);

    for (const { label } of Object.values(SENDER_FIELDS)) {
      expect(field(label)).toBeDisabled();
    }
    // Блок набора раскрывается и без права — смотреть можно всем; поля в нём закрыты.
    await userEvent.setup().click(screen.getByRole('button', { name: 'Письма на английском' }));
    const english = screen.getByRole('group', { name: 'Письма на английском' });
    await within(english).findByText('Пусто — в письмах на этом языке будет общее поле');
    for (const input of within(english).getAllByRole('textbox')) {
      expect(input).toBeDisabled();
    }
    expect(field('Подпись')).toHaveValue('Ива Тестова');
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(screen.getByText(LINE)).toBeInTheDocument();
    expect(screen.getByText('Отправка продаж не готова')).toBeInTheDocument();
    expect(recorded.calls.filter((call) => call.method === 'POST')).toEqual([]);
  });

  it('с правом отправки строки нет, поля открыты', async () => {
    await openSender();

    expect(screen.queryByText(LINE)).not.toBeInTheDocument();
    expect(field('Подпись')).toBeEnabled();
  });
});
