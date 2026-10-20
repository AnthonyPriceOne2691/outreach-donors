/**
 * «Форма и письмо» на «Формах» (20.10.2026): прямая ссылка на страницу формы и текст —
 * тот же, что ушёл бы донору письмом, с копированием темы и текста. Текст собирается
 * задачей и хранится; форму отправляет человек — окно так и говорит.
 *
 * Внутри окна проверяется присутствие, а не видимость: окно Mantine появляется переходом,
 * и в jsdom его прозрачность в первые кадры — ноль.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { FormLetterView } from '../api/deeper';
import type { JobCard, Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Recorded } from '../test/server';

/** Записанные ответы — и функцией: ответ меняется посреди теста. */
type Routes = Parameters<typeof serve>[0];

const FORM = 'https://form.example.test/contact-us/';

const QUEUE = {
  rows: [
    {
      donor_id: 7,
      domain_id: 70,
      host: 'form.example.test',
      dr: 55,
      org_traffic: 9000,
      attempted_at: '2026-10-20T10:00:00+00:00',
      form_url: FORM,
    },
    {
      donor_id: 8,
      domain_id: 80,
      host: 'old.example.test',
      dr: 40,
      org_traffic: 900,
      attempted_at: '2026-09-20T10:00:00+00:00',
      form_url: null,
    },
  ],
  total: 2,
  page: 1,
  limit: 20,
  monthly_left: 98,
  monthly_cap: 100,
  advertisers_waiting: 0,
  retry_days: 180,
};

const EMPTY_VIEW: FormLetterView = {
  donor_id: 7,
  host: 'form.example.test',
  form_url: FORM,
  letter: null,
  job_id: null,
};

const LETTER: FormLetterView = {
  ...EMPTY_VIEW,
  letter: {
    subject: 'Guest article on form.example.test',
    body: 'Hello,\n\nI found your site while reading about small business taxes.',
    based_on: 'текст рассылки «осень»',
    built_at: '2026-10-20T11:00:00+00:00',
    uniqueness_pct: 0.2,
    notes: null,
    unset: [],
  },
};

function job(state: JobCard['state'], title: string): JobCard {
  return {
    job_id: 'form-letter-7',
    kind: 'текст письма для формы',
    state,
    title,
    error: null,
    report: null,
    retries_left: 3,
    next_try_at: null,
    ended_at: null,
  };
}

async function openForms(routes: Routes = {}, who: Me = ADMIN): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/contacts/forms?page=1': { body: QUEUE },
    ...routes,
  });
  renderWith(<AppRoutes />, '/forms');
  await screen.findByText('form.example.test');
  return recorded;
}

function rowOf(host: string): HTMLElement {
  return screen.getByText(host).closest('tr')!;
}

describe('«Формы»: ссылка на форму', () => {
  it('форма, которую запомнила лестница, — прямой ссылкой под именем сайта', async () => {
    await openForms();

    expect(
      within(rowOf('form.example.test')).getByRole('link', { name: 'открыть форму' }),
    ).toHaveAttribute('href', FORM);
    expect(
      within(rowOf('old.example.test')).queryByRole('link', { name: 'открыть форму' }),
    ).toBeNull();
  });
});

describe('«Формы»: окно «Форма и письмо»', () => {
  it('текст ещё не собран — сборка задачей, и собранный текст появляется в окне', async () => {
    let view: FormLetterView = EMPTY_VIEW;
    const recorded = await openForms({
      'GET /api/contacts/forms/7/letter': () => ({ body: view }),
      'POST /api/contacts/forms/7/letter': { body: { ...EMPTY_VIEW, job_id: 'form-letter-7' } },
      'GET /api/jobs/form-letter-7': () => ({
        body: job(view.letter ? 'done' : 'running', view.letter ? 'готово' : 'идёт'),
      }),
    });
    const user = userEvent.setup();

    await user.click(
      within(rowOf('form.example.test')).getByRole('button', { name: 'Форма и письмо' }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Форма и письмо: form.example.test' });
    expect(within(dialog).getByRole('link', { name: 'открыть страницу формы' })).toHaveAttribute(
      'href',
      FORM,
    );
    expect(
      within(dialog).getByText(/Форму отправляете вы: сервис форм не заполняет/),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Собрать текст' }));

    expect(await within(dialog).findByText('Текст письма для формы: идёт')).toBeInTheDocument();
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => [call.path, call.body])).toEqual([
      ['/api/contacts/forms/7/letter', { again: false }],
    ]);
    view = LETTER;
    expect(
      await within(dialog).findByDisplayValue(
        'Guest article on form.example.test',
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Скопировать текст' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Скопировать тему' })).toBeInTheDocument();
    expect(within(dialog).getByText(/Основа — текст рассылки «осень»/)).toBeInTheDocument();
  });

  it('собранный текст — сразу, и «Собрать заново» просит новый', async () => {
    const recorded = await openForms({
      'GET /api/contacts/forms/7/letter': { body: LETTER },
      'POST /api/contacts/forms/7/letter': { body: { ...LETTER, job_id: 'form-letter-7' } },
      'GET /api/jobs/form-letter-7': { body: job('running', 'идёт') },
    });
    const user = userEvent.setup();

    await user.click(
      within(rowOf('form.example.test')).getByRole('button', { name: 'Форма и письмо' }),
    );
    const dialog = await screen.findByRole('dialog');
    expect(
      await within(dialog).findByDisplayValue(/I found your site while reading/),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Собрать заново' }));

    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => call.body)).toEqual([{ again: true }]);
  });

  it('метка незаполненной настройки в тексте — предупреждением', async () => {
    await openForms({
      'GET /api/contacts/forms/7/letter': {
        body: { ...LETTER, letter: { ...LETTER.letter!, unset: ['ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО'] } },
      },
    });
    const user = userEvent.setup();

    await user.click(
      within(rowOf('form.example.test')).getByRole('button', { name: 'Форма и письмо' }),
    );

    expect(
      await screen.findByText(/«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО» — настройка не заполнена/),
    ).toBeInTheDocument();
  });

  it('адрес формы не записан — окно ведёт на сайт и говорит почему', async () => {
    await openForms({
      'GET /api/contacts/forms/8/letter': {
        body: { ...EMPTY_VIEW, donor_id: 8, host: 'old.example.test', form_url: null },
      },
    });
    const user = userEvent.setup();

    await user.click(
      within(rowOf('old.example.test')).getByRole('button', { name: 'Форма и письмо' }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/Где форма, не записано/)).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'Открыть сайт' })).toHaveAttribute(
      'href',
      'https://old.example.test',
    );
  });

  it('без права «запускать прогоны» строки без кнопок — и окна нет', async () => {
    await openForms({}, { ...OPERATOR, permissions: ['view'] });

    expect(screen.queryByRole('button', { name: 'Форма и письмо' })).toBeNull();
  });

  it('у рекламодателей текста письма для формы нет — только ссылка на форму', async () => {
    await openForms({
      'GET /api/advertisers/forms?page=1': {
        body: {
          rows: [
            {
              advertiser_id: 3,
              domain_id: 30,
              host: 'brand.example.test',
              source: 'links',
              donor_host: 'donor.example.test',
              run_id: null,
              points: 4,
              attempted_at: '2026-10-20T10:00:00+00:00',
              form_url: 'https://brand.example.test/kontakt/',
            },
          ],
          total: 1,
          page: 1,
          limit: 20,
          monthly_left: 98,
          monthly_cap: 100,
          donors_waiting: 2,
          retry_days: 180,
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: /Рекламодатели/ }));

    const row = (await screen.findByText('brand.example.test')).closest('tr')!;
    expect(within(row).getByRole('link', { name: 'открыть форму' })).toHaveAttribute(
      'href',
      'https://brand.example.test/kontakt/',
    );
    expect(within(row).queryByRole('button', { name: 'Форма и письмо' })).toBeNull();
    expect(within(row).getByRole('button', { name: 'Вписать адрес' })).toBeVisible();
  });
});
