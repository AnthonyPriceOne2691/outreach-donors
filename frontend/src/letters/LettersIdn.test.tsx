/**
 * Домен IDN на «Письмах» (18.11.2026): база хранит имя хоста в ASCII, а очередь и предпросмотр
 * письма — как его пишут люди: «пример.рф», а не «xn--e1afmkfd.xn--p1ai». Данные выдуманные.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const LETTER = {
  id: 7,
  host: 'xn--e1afmkfd.xn--p1ai',
  email: 'info@xn--e1afmkfd.xn--p1ai',
  campaign: 'Демонстрация',
  status: 'queued' as const,
  subject: 'Размещение статьи',
  body: 'Добрый день,\n\nС уважением,\nАнна',
  uniqueness: 0.19,
  verdict: null,
  followups: [],
};

const VIEW = {
  letters: [LETTER],
  followup_default: [7, 14],
  letter_default: { subject: 'Статья на {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 1, 'с адресом': 1, 'ещё не писали': 0 },
  queued_total: 1,
  batch_max: 200,
};

describe('домен IDN на «Письмах»', () => {
  it('строка очереди и предпросмотр письма — домен и адрес раскодированы', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/letters': { body: VIEW },
      'GET /api/runs/with-accepted': { body: [] },
      ...NO_STUCK_LETTERS,
    });
    renderWith(<AppRoutes />, '/letters');

    // Строка очереди и шапка предпросмотра.
    expect(await screen.findAllByText('пример.рф')).toHaveLength(2);
    expect(screen.getByText('info@пример.рф')).toBeInTheDocument();
    expect(screen.getByText(/^info@пример\.рф · кампания «Демонстрация»/)).toBeInTheDocument();
    expect(screen.queryByText(/xn--/)).not.toBeInTheDocument();
  });
});
