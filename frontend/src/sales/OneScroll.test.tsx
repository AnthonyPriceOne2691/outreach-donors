/**
 * Окна раздела «Продажи» — одна прокрутка (правило «В окне одна прокрутка», `docs/UI_RULES.md`;
 * замечание Anthony 12.10.2026 к окну письма: «двойная прокрутка… прям косяк»). Замер стенда
 * 13.10.2026: в окнах записи базы знаний, шага цепочки, стоп-листа, гипотезы и лида
 * прокручивались и окно, и поле под ним, а кнопки уезжали под край экрана; в «Что увидит агент»
 * текст записей проезжал под прозрачной шапкой.
 *
 * Как у «Текста первого письма» (`letters/LetterDraftEditor.test.tsx`): раскладку jsdom не
 * считает — проверяется, что прокрутка в окне одна, что в ней и что вне её. Отказ сервера —
 * над прокруткой, кнопки и строка «почему закрыто» — под ней; письмо глазами адресата и
 * непринятые строки после нажатия подъезжают на виду. Сколько прокруток на самом деле и видны
 * ли кнопки — замер в браузере (1440 × 900, 1280 × 720, 390 × 844, 375 × 667, 844 × 390).
 * Ответы сервера — записанные (`serve()`), данные выдуманы.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { SalesStoplistAdded } from '../api/salesStoplist';
import type {
  AgentView,
  ChainPreviewView,
  ChainStepCard,
  ChainView,
  KbEntryCard,
  LeadCard,
} from '../api/salesTypes';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { AgentPreview } from './AgentPreview';
import { ChainStepModal } from './ChainStepModal';
import { HypothesisModal } from './HypothesisModal';
import { KbEntryModal } from './KbEntryModal';
import { LeadEditModal } from './LeadEditModal';
import { StoplistAddModal } from './StoplistAddModal';

type User = ReturnType<typeof userEvent.setup>;

const SCREEN_WAIT = { timeout: 5000 };
const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
const NOTHING = () => undefined;

const ENTRY: KbEntryCard = {
  id: 11,
  kind: 'brief',
  language: 'en',
  title: 'Test brief',
  text: 'Made-up text of the entry.',
  tags: [],
  active: true,
  updated_by: null,
  created_at: '2026-10-05T09:00:00+00:00',
  updated_at: '2026-10-05T09:00:00+00:00',
};

const STEP: ChainStepCard = {
  id: 21,
  hypothesis_id: null,
  step: 1,
  language: 'en',
  subject: 'Test for {{company}}',
  body: '[greeting] rewrite\nHello {{name}},\n\n[offer] fixed\nTest offer body.',
  zones: [
    { name: 'greeting', kind: 'rewrite', text: 'Hello {{name}},' },
    { name: 'offer', kind: 'fixed', text: 'Test offer body.' },
  ],
  warning: null,
  active: true,
  updated_by: null,
  created_at: '2026-10-05T09:00:00+00:00',
  updated_at: '2026-10-05T09:00:00+00:00',
};

const VIEW: ChainView = {
  hypothesis_id: null,
  rows: [STEP],
  chains: [],
  steps: [1, 2, 3],
  languages: ['en'],
  placeholders: ['name', 'company', 'site'],
  limits: { subject: 255, body: 10000 },
};

const LEAD: LeadCard = {
  id: 17,
  email: 'olga@vega.example.test',
  name: null,
  position: null,
  company: 'Vega',
  host: 'vega.example.test',
  country: 'de',
  timezone: null,
  language: 'en',
  hypothesis_id: 4,
  hypothesis: 'своя база',
  source: 'referral',
  status: 'ready',
  rejection_reason: null,
  cleaning_note: null,
  verification_status: 'fixture:valid',
  created_at: '2026-10-05T09:15:00+00:00',
};

/** Окно раздела: что в середине, что под ней и чем его записывают. */
interface SalesDialog {
  name: string;
  ui: (mayWrite: boolean) => ReactElement;
  title: string | RegExp;
  fields: string[];
  buttons: string[];
  /** Правка, после которой кнопка записи живая, и нажатие. */
  submit: (user: User, dialog: HTMLElement) => Promise<void>;
  /** Запрос записи — ему отвечает отказ. */
  route: string;
  /** Заголовок отказа. */
  refusal: string;
}

async function typeAndPress(
  user: User,
  dialog: HTMLElement,
  field: string,
  text: string,
  button: string,
) {
  await user.type(within(dialog).getByLabelText(field), text);
  await user.click(within(dialog).getByRole('button', { name: button }));
}

const KB_DIALOG: SalesDialog = {
  name: 'запись базы знаний',
  ui: (mayWrite) => (
    <KbEntryModal
      entry={ENTRY}
      kinds={['brief', 'service']}
      limits={{ title: 255, text: 20000, tag: 64, tags: 20 }}
      mayWrite={mayWrite}
      onClose={NOTHING}
      onSaved={NOTHING}
    />
  ),
  title: 'Запись №11',
  fields: ['Вид', 'Язык', 'Заголовок', 'Текст', 'Теги', 'Агент видит запись'],
  buttons: ['Отмена', 'Сохранить'],
  submit: (user, dialog) => typeAndPress(user, dialog, 'Заголовок', ' 2', 'Сохранить'),
  route: 'PATCH /api/sales/kb/11',
  refusal: 'Не сохранили',
};

const CHAIN_DIALOG: SalesDialog = {
  name: 'шаг цепочки',
  ui: (mayWrite) => (
    <ChainStepModal
      place={{ hypothesisId: null, step: 1, language: 'en' }}
      setTitle="общий набор"
      row={STEP}
      view={VIEW}
      mayWrite={mayWrite}
      onClose={NOTHING}
    />
  ),
  title: 'Первое письмо · Английский — общий набор',
  fields: ['Тема', 'Текст письма', 'Шаг включён — входит в цепочку'],
  buttons: ['Показать письмо', 'Отмена', 'Сохранить'],
  submit: (user, dialog) => typeAndPress(user, dialog, 'Тема', ' more', 'Сохранить'),
  route: 'POST /api/sales/chain',
  refusal: 'Не сохранили',
};

const STOPLIST_DIALOG: SalesDialog = {
  name: 'стоп-лист',
  ui: () => (
    <StoplistAddModal noteMax={200} onClose={NOTHING} onDone={NOTHING} onPartly={NOTHING} />
  ),
  title: 'Добавить в стоп-лист продаж',
  fields: ['Адреса и домены — по строке', 'Или файл CSV', 'Причина'],
  buttons: ['Отмена', 'Добавить'],
  submit: (user, dialog) =>
    typeAndPress(user, dialog, 'Адреса и домены — по строке', 'acme.example.test', 'Добавить'),
  route: 'POST /api/sales/stoplist',
  refusal: 'Не сохранили',
};

const HYPOTHESIS_DIALOG: SalesDialog = {
  name: 'новая гипотеза',
  ui: () => <HypothesisModal onClose={NOTHING} onAdded={NOTHING} />,
  title: 'Новая гипотеза',
  fields: ['Имя', 'Описание'],
  buttons: ['Отмена', 'Завести'],
  submit: (user, dialog) => typeAndPress(user, dialog, 'Имя', 'сайты EN', 'Завести'),
  route: 'POST /api/sales/hypotheses',
  refusal: 'Гипотеза не заведена',
};

const LEAD_DIALOG: SalesDialog = {
  name: 'лид',
  ui: (mayWrite) => (
    <LeadEditModal lead={LEAD} mayWrite={mayWrite} onClose={NOTHING} onSaved={NOTHING} />
  ),
  title: 'Лид olga@vega.example.test',
  fields: ['Имя', 'Должность', 'Компания', 'Язык письма', 'Страна', 'Часовой пояс'],
  buttons: ['Отмена', 'Сохранить'],
  submit: (user, dialog) => typeAndPress(user, dialog, 'Имя', 'Olga', 'Сохранить'),
  route: 'PATCH /api/sales/leads/17',
  refusal: 'Не сохранили',
};

const DIALOGS = [KB_DIALOG, CHAIN_DIALOG, STOPLIST_DIALOG, HYPOTHESIS_DIALOG, LEAD_DIALOG];

async function open(dialog: SalesDialog, mayWrite = true): Promise<HTMLElement> {
  renderWith(dialog.ui(mayWrite));
  return screen.findByRole('dialog', { name: dialog.title }, SCREEN_WAIT);
}

/** Середина окна — единственная прокрутка; `fields` — в ней, `buttons` — вне её. */
function onlyScroll(dialog: HTMLElement, fields: string[], buttons: string[]): HTMLElement {
  const scrollers = dialog.querySelectorAll<HTMLElement>('.oneScrollArea');
  expect(scrollers).toHaveLength(1);
  // Прокрутка самого окна Mantine или `ScrollArea` поверх неё — вторая.
  expect(dialog.querySelector('.mantine-ScrollArea-root')).toBeNull();
  expect(dialog).toHaveClass('oneScroll');
  const middle = scrollers[0] as HTMLElement;
  for (const field of fields) {
    expect(within(middle).getByLabelText(field)).toBeInTheDocument();
  }
  for (const button of buttons) {
    expect(within(dialog).getByRole('button', { name: button })).toBeInTheDocument();
    expect(within(middle).queryByRole('button', { name: button })).toBeNull();
  }
  return middle;
}

describe('окна продаж — одна прокрутка', () => {
  it.each(DIALOGS)('$name: поля — в середине, она одна; кнопки — под ней', async (shown) => {
    const dialog = await open(shown);

    onlyScroll(dialog, shown.fields, shown.buttons);
  });

  it.each(DIALOGS)(
    '$name: отказ сервера — над прокруткой: кнопка внизу, отказ виден при любой прокрутке',
    async (shown) => {
      serve({ [shown.route]: { status: 400, body: { detail: 'выдуманный отказ сервера' } } });
      const dialog = await open(shown);

      await shown.submit(userEvent.setup(), dialog);

      const refusal = await within(dialog).findByText(shown.refusal, {}, SCREEN_WAIT);
      const middle = onlyScroll(dialog, shown.fields, shown.buttons);
      expect(middle).not.toContainElement(refusal);
      expect(refusal.compareDocumentPosition(middle) & FOLLOWING).toBeTruthy();
    },
  );

  it.each([KB_DIALOG, CHAIN_DIALOG, LEAD_DIALOG])(
    '$name без права отправки: «почему закрыто» — строкой под кнопками, вне прокрутки',
    async (shown) => {
      const dialog = await open(shown, false);

      const middle = onlyScroll(dialog, shown.fields, shown.buttons);
      const why = within(dialog).getByText(/тот, у кого есть право отправки писем/);
      expect(middle).not.toContainElement(why);
      const save = within(dialog).getByRole('button', { name: 'Сохранить' });
      expect(save).toBeDisabled();
      expect(save.compareDocumentPosition(why) & FOLLOWING).toBeTruthy();
    },
  );

  it('шаг цепочки: пояснение шага — первым абзацем той же прокрутки', async () => {
    const dialog = await open(CHAIN_DIALOG);

    const middle = onlyScroll(dialog, CHAIN_DIALOG.fields, CHAIN_DIALOG.buttons);
    const hint = within(middle).getByText(/^с темой; зоны rewrite модель может переписать/);
    expect(
      hint.compareDocumentPosition(within(middle).getByLabelText('Тема')) & FOLLOWING,
    ).toBeTruthy();
  });

  it('стоп-лист: что станет с лидами — над кнопкой, вне прокрутки: длинный список её не отодвинет', async () => {
    const dialog = await open(STOPLIST_DIALOG);

    const middle = onlyScroll(dialog, STOPLIST_DIALOG.fields, STOPLIST_DIALOG.buttons);
    const note = within(dialog).getByText(/^Лиды, которых закроют новые записи, снимутся сразу/);
    expect(middle).not.toContainElement(note);
    const add = within(dialog).getByRole('button', { name: 'Добавить' });
    expect(note.compareDocumentPosition(add) & FOLLOWING).toBeTruthy();
  });

  it('«Что увидит агент»: записи — в середине, окно само не прокручивается', async () => {
    const seen: AgentView = {
      version: 'kb-000000000001',
      total: 1,
      groups: [
        {
          kind: 'brief',
          language: 'en',
          facts: [{ id: 1, title: 'Кто мы', text: 'Выдуманный текст записи.', tags: [] }],
        },
      ],
    };
    serve({ 'GET /api/sales/kb/preview': { body: seen } });
    renderWith(<AgentPreview onClose={NOTHING} />);
    const dialog = await screen.findByRole('dialog', { name: 'Что увидит агент' }, SCREEN_WAIT);

    const fact = await within(dialog).findByText('Кто мы', {}, SCREEN_WAIT);
    const middle = onlyScroll(dialog, [], []);
    expect(middle).toContainElement(fact);
    expect(middle).toContainElement(within(dialog).getByText(/^Версия базы/));
  });

  it('поля окон — без потолка строк: поле длиннее потолка прокручивалось бы само', () => {
    for (const file of ['KbEntryModal', 'ChainStepModal', 'StoplistAddModal', 'HypothesisModal']) {
      const source = readFileSync(resolve(process.cwd(), `src/sales/${file}.tsx`), 'utf8');
      expect(source, file).toMatch(/<Textarea/);
      expect(source, file).not.toMatch(/maxRows=\{/);
    }
  });
});

/**
 * Раскладка, которой jsdom не считает: верх середины — на 100 px от верха экрана, верх всего
 * прочего — на 640. Подъезд читает только два прямоугольника — середины и показанного.
 */
function placed(): void {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const top = this.classList.contains('oneScrollArea') ? 100 : 640;
    return { top, bottom: top, left: 0, right: 0, x: 0, y: top, width: 0, height: 0 } as DOMRect;
  });
}

/** Середина прокручена на 500 px, и показанное встало на 540 px ниже её верха. */
const SCROLLED = 500;
const REVEALED = SCROLLED + 640 - 100;

describe('окна продаж — показанное после нажатия подъезжает на виду', () => {
  it('шаг цепочки: письмо глазами адресата — в конце прокрутки, и середина подъезжает к нему', async () => {
    const letter: ChainPreviewView = {
      subject: 'Test for Example Company',
      zones: [{ name: 'greeting', kind: 'rewrite', text: 'Hello Alex Example,' }],
      values: { name: 'Alex Example', company: 'Example Company', site: 'example.com' },
      sender_name: 'Iva Testova',
      signature: 'Iva Testova',
      address: null,
      missing: [],
      warning: null,
    };
    serve({ 'POST /api/sales/chain/preview': { body: letter } });
    const dialog = await open(CHAIN_DIALOG);
    const middle = onlyScroll(dialog, CHAIN_DIALOG.fields, CHAIN_DIALOG.buttons);
    middle.scrollTop = SCROLLED;
    placed();

    await userEvent.setup().click(within(dialog).getByRole('button', { name: 'Показать письмо' }));

    const shown = await within(dialog).findByLabelText('Письмо глазами адресата', {}, SCREEN_WAIT);
    expect(middle).toContainElement(shown);
    await waitFor(() => expect(middle.scrollTop).toBe(REVEALED));
  });

  it('стоп-лист: непринятые строки — в начале прокрутки, и середина подъезжает к ним', async () => {
    const partly: SalesStoplistAdded = {
      added: 1,
      known: 0,
      closed: 0,
      remarks: [{ line: 2, reason: 'не домен и не адрес', cell: 'не домен' }],
    };
    serve({ 'POST /api/sales/stoplist': { body: partly } });
    const dialog = await open(STOPLIST_DIALOG);
    const middle = onlyScroll(dialog, STOPLIST_DIALOG.fields, STOPLIST_DIALOG.buttons);
    middle.scrollTop = SCROLLED;
    placed();

    await STOPLIST_DIALOG.submit(userEvent.setup(), dialog);

    const title = await within(dialog).findByText('Не все строки легли', {}, SCREEN_WAIT);
    expect(middle).toContainElement(title);
    await waitFor(() => expect(middle.scrollTop).toBe(REVEALED));
  });

  it('стоп-лист: правка списка после замечаний — середина стоит, к замечаниям не прыгает', async () => {
    const partly: SalesStoplistAdded = {
      added: 1,
      known: 0,
      closed: 0,
      remarks: [{ line: 2, reason: 'не домен и не адрес', cell: 'не домен' }],
    };
    serve({ 'POST /api/sales/stoplist': { body: partly } });
    const dialog = await open(STOPLIST_DIALOG);
    const middle = onlyScroll(dialog, STOPLIST_DIALOG.fields, STOPLIST_DIALOG.buttons);
    const user = userEvent.setup();
    await STOPLIST_DIALOG.submit(user, dialog);
    await within(dialog).findByText('Не все строки легли', {}, SCREEN_WAIT);
    // Человек ушёл вниз, к непринятой строке в поле, и правит её.
    middle.scrollTop = SCROLLED;
    placed();

    await user.type(within(dialog).getByLabelText('Адреса и домены — по строке'), '.example.test');

    expect(middle.scrollTop).toBe(SCROLLED);
  });
});
