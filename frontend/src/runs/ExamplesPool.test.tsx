/**
 * «Собрать моделью» по примерам сайтов на экране прогона: человек вписывает сайты,
 * сервер открывает их главные и называет тему, ключи падают в поле ключей.
 *
 * Проверяется то, что видно и что уходит: отказ поля — до нажатия и словами сервера,
 * строки — как вписаны, итог называет тему, каждый пример с исходом и токены, а
 * сборка ничего платного не трогает — ни сметы, ни запуска.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { openRun } from '../test/runFixtures';

const ROUTES = {
  'GET /api/keywords/presets': { body: ['guest', 'reviews', 'wide'] },
  'GET /api/keywords/languages?country=us': { body: ['English'] },
};

/** Ответ сервера: два примера прочитаны (один — не про общую тему), один — нет. */
const BUILT = {
  keywords: ['home gardening blog', 'balcony vegetables tips'],
  languages: ['English'],
  asked: 12,
  received: 10,
  rejected: 0,
  near_duplicates: 1,
  refusals: [],
  tokens: 3412,
  model: 'gpt-5',
  theme: 'блоги о домашнем садоводстве',
  topics: { English: 'home gardening' },
  sites: [
    {
      host: 'garden.example.test',
      read: true,
      reason: '',
      title: 'Garden friends',
      off_theme: false,
    },
    {
      host: 'сад.example.test',
      read: true,
      reason: '',
      title: 'Сад на балконе',
      off_theme: true,
    },
    {
      host: 'gone.example.test',
      read: false,
      reason: 'не открылся: имя не найдено или соединение отклонено',
      title: '',
      off_theme: false,
    },
  ],
  repeats: 1,
  theme_tokens: 1210,
};

async function openExamples(extra: Record<string, unknown> = {}) {
  const recorded = await openRun({ ...ROUTES, ...extra });
  const user = userEvent.setup();
  await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
  await user.click(await screen.findByRole('radio', { name: 'По примерам сайтов' }));
  return { recorded, user };
}

describe('сборка по примерам сайтов', () => {
  it('поле примеров встаёт на место темы, кнопка закрыта, пока поле пусто', async () => {
    await openExamples();

    expect(screen.getByLabelText('Примеры сайтов')).toBeInTheDocument();
    expect(screen.queryAllByLabelText('Про что')).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Собрать по примерам' })).toBeDisabled();
  });

  it('негодная строка — отказ до нажатия, словами сервера и с номером строки', async () => {
    const { recorded, user } = await openExamples();

    await user.type(screen.getByLabelText('Примеры сайтов'), 'garden.example.test{enter}не сайт');

    expect(await screen.findByText(/строка 2: «не сайт» — пробел внутри/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать по примерам' })).toBeDisabled();
    expect(recorded.calls.some((call) => call.path === '/api/keywords/examples')).toBe(false);
  });

  it('под полем — сколько сайтов и сколько повторов не в счёт', async () => {
    const { user } = await openExamples();

    await user.type(
      screen.getByLabelText('Примеры сайтов'),
      'garden.example.test{enter}https://www.garden.example.test/blog{enter}news.example.test',
    );

    expect(screen.getByText('2 сайта из 10 · 1 повтор не в счёт')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать по примерам' })).toBeEnabled();
  });

  it('строки уходят как вписаны, ключи падают в поле, итог называет, на чём стоит тема', async () => {
    const { recorded, user } = await openExamples({
      'POST /api/keywords/examples': { body: BUILT },
    });

    await user.type(
      screen.getByLabelText('Примеры сайтов'),
      'garden.example.test{enter}{enter}сад.example.test{enter}gone.example.test',
    );
    await user.click(screen.getByRole('button', { name: 'Собрать по примерам' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Ключевые слова')).toHaveValue(
        'home gardening blog\nbalcony vegetables tips',
      ),
    );
    const call = recorded.calls.find((item) => item.path === '/api/keywords/examples');
    // Пустая строка — тоже строка: отказ сервера назовёт строку её номером в поле.
    expect(call?.body).toEqual({
      examples: ['garden.example.test', '', 'сад.example.test', 'gone.example.test'],
      preset: 'wide',
      country: 'us',
      cap: 30,
    });

    const outcome = screen.getByLabelText('Итог сборки по примерам');
    expect(within(outcome).getByText('Тема примеров: блоги о домашнем садоводстве')).toBeVisible();
    expect(within(outcome).getByText(/Ключи — про английский — «home gardening»/)).toBeVisible();
    expect(within(outcome).getByText(/по 2 из 3 примеров · 1 повтор не в счёт/)).toBeVisible();
    expect(within(outcome).getByText(/— прочитан: «Garden friends»/)).toBeVisible();
    expect(within(outcome).getByText(/— прочитан, но не про общую тему/)).toBeVisible();
    expect(within(outcome).getByText(/— не прочитан: не открылся: имя не найдено/)).toBeVisible();
    // Разряды — неразрывным пробелом, как у всех чисел сервиса (поиск по тексту
    // схлопывает его в обычный — поэтому сверяется текст узла).
    const tokens = within(outcome).getByText(/Модель gpt-5: 3 412 токенов, из них на тему — 1 210/);
    expect(tokens.textContent).toContain('3\u00a0412');

    // Сборка ничего платного не трогает: ни сметы, ни запуска.
    expect(recorded.calls.some((call) => call.path === '/api/runs/estimate')).toBe(false);
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('отказ сервера виден его словами, а поле ключей не тронуто', async () => {
    const { user } = await openExamples({
      'POST /api/keywords/examples': {
        status: 422,
        body: {
          detail:
            'Ни одна главная примеров не прочитана — тему вывести не из чего: gone.example.test — не ответил вовремя. Дайте другие примеры или соберите ключи по теме',
        },
      },
    });

    await user.type(screen.getByLabelText('Ключевые слова'), 'свой ключ');
    await user.type(screen.getByLabelText('Примеры сайтов'), 'gone.example.test');
    await user.click(screen.getByRole('button', { name: 'Собрать по примерам' }));

    expect(await screen.findByText('Ключи по примерам не собрались')).toBeInTheDocument();
    expect(screen.getByText(/Ни одна главная примеров не прочитана/)).toBeInTheDocument();
    expect(screen.getByLabelText('Ключевые слова')).toHaveValue('свой ключ');
  });

  it('назад к теме — поле тем на месте, а вписанные примеры не теряются', async () => {
    const { user } = await openExamples();

    await user.type(screen.getByLabelText('Примеры сайтов'), 'garden.example.test');
    await user.click(screen.getByRole('radio', { name: 'По теме' }));

    expect(screen.queryByLabelText('Примеры сайтов')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать' })).toBeEnabled();

    await user.click(screen.getByRole('radio', { name: 'По примерам сайтов' }));
    expect(screen.getByLabelText('Примеры сайтов')).toHaveValue('garden.example.test');
  });
});
