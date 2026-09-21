/**
 * Экран писем: что видно до нажатия и что уходит на сервер.
 *
 * Проверяется не «отрисовалось», а три обещания экрана: текст письма
 * виден целиком, отправка не нажимается, пока её что-то блокирует,
 * и ненастоящий транспорт называет себя вслух. Все три — про то, чтобы
 * человек не узнал о препятствии после нажатия.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const LETTER = {
  id: 7,
  host: 'digest-weekly.example.test',
  email: 'editor@digest-weekly.example.test',
  campaign: 'Демонстрация',
  status: 'queued',
  subject: 'Advertising rates for digest-weekly.example.test',
  body: 'Good afternoon to you,\n\nI have been reading digest-weekly.example.test.\n\nBest regards,\nAnna Ro',
  uniqueness: 0.19,
  verdict: null,
};

const OFF_CORRIDOR = {
  ...LETTER,
  id: 8,
  host: 'city-news.example.test',
  email: 'info@city-news.example.test',
  uniqueness: 0.04,
  verdict: 'отличие 4% ниже коридора 15–25%: письмо слишком похоже на шаблон',
};

const VIEW = {
  letters: [LETTER, OFF_CORRIDOR],
  blocked_by: [],
  transport: { name: 'null', real: false, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 12, 'с адресом': 9, 'вне стоп-листа': 9, 'ещё не писали': 2 },
};

async function openLetters(
  view: Record<string, unknown> = {},
  routes: Record<string, unknown> = {},
  who = ADMIN,
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/letters': { body: { ...VIEW, ...view } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/letters');
  await screen.findByRole('heading', { name: 'Письма' });
  return recorded;
}

describe('очередь писем', () => {
  it('текст письма виден целиком, а не в виде сводки', async () => {
    await openLetters();

    // Смысл экрана в том, что спорное решение видит человек: пересказ
    // письма вместо письма этот смысл отменяет.
    expect(screen.getByText(/Best regards/)).toBeInTheDocument();
    expect(screen.getByText(/Good afternoon to you/)).toBeInTheDocument();
  });

  it('ненастоящий транспорт называет себя до нажатия', async () => {
    await openLetters();

    // Письмо, помеченное отправленным и никуда не ушедшее, выглядит
    // как работа — и это самая дорогая ложь на экране.
    expect(screen.getByText('Наружу письмо не уйдёт')).toBeInTheDocument();
  });

  it('незаполненный юридический блок блокирует кнопку', async () => {
    await openLetters({ blocked_by: ['OUTREACH_POSTAL_ADDRESS', 'OUTREACH_UNSUBSCRIBE_URL'] });

    // Наверху — почему так, у кнопки — почему она не нажимается. Разными
    // словами: два одинаковых предупреждения на экране читаются как сбой.
    expect(screen.getByText(/OUTREACH_UNSUBSCRIBE_URL/)).toBeInTheDocument();
    expect(screen.getByText(/Кнопка не нажимается/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
  });

  it('отличие вне коридора объясняется словами, а не только цветом', async () => {
    await openLetters();
    const user = userEvent.setup();

    await user.click(screen.getByText('city-news.example.test'));

    expect(screen.getByText(/ниже коридора 15–25%/)).toBeInTheDocument();
  });

  it('отправка уходит по одному письму', async () => {
    const recorded = await openLetters(
      { blocked_by: [] },
      {
        'POST /api/letters/7/send': {
          body: { id: 7, sender_email: 'outreach1@mail-alpha.example.test', real: false },
        },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await screen.findByText(/наружу НЕ ушло/);
    const posts = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0]?.path).toBe('/api/letters/7/send');
  });

  it('правка отправляет новый текст и показывает пересчитанный процент', async () => {
    const recorded = await openLetters(
      {},
      {
        'PATCH /api/letters/7': { body: { ...LETTER, uniqueness: 0.22 } },
      },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Поправить' }));
    const field = screen.getByLabelText('Текст письма');
    await user.clear(field);
    await user.type(field, 'Другое письмо целиком');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await screen.findByText(/отличие от шаблона — 22%/);
    const patch = recorded.calls.find((call: Call) => call.method === 'PATCH');
    expect(patch?.path).toBe('/api/letters/7');
  });

  it('«не писать» предупреждает, что донор больше не появится', async () => {
    await openLetters(
      {},
      { 'POST /api/letters/7/skip': { body: { ...LETTER, status: 'stopped' } } },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не писать' }));

    expect(await screen.findByText(/в следующей сборке не появится/)).toBeInTheDocument();
  });

  it('пустая очередь объясняет, где кончились доноры', async () => {
    await openLetters({
      letters: [],
      funnel: { подходящих: 12, 'с адресом': 0, 'ещё не писали': 0 },
    });

    // Пустая очередь при «всем написали» и при «ни у кого нет адреса»
    // выглядит одинаково, а действия из них следуют разные.
    expect(screen.getByText('Очередь пуста')).toBeInTheDocument();
    expect(screen.getByText(/пора добрать контакты/)).toBeInTheDocument();
  });

  it('без права на отправку очередь видна, а кнопки нет', async () => {
    await openLetters({}, {}, OPERATOR);

    // Донор виден дважды и это норма: строка очереди и заголовок письма.
    expect(screen.getAllByText('digest-weekly.example.test')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
    expect(screen.getByText(/отдельное право/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Собрать очередь' })).not.toBeInTheDocument();
  });

  it('коридор берётся с сервера, а не из числа на фронте', async () => {
    await openLetters({ corridor: { min: 0.1, max: 0.4 } });

    const tile = screen.getByText('Вне коридора').closest('div');
    expect(within(tile as HTMLElement).getByText('коридор 10–40%')).toBeInTheDocument();
  });
});
