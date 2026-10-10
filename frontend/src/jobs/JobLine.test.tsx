import { useQueryClient } from '@tanstack/react-query';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { JobCard } from '../api/types';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { JobLine, jobRestarted, jobSentence } from './JobLine';

const BASE: JobCard = {
  job_id: 'job-7',
  kind: 'сборка писем',
  state: 'done',
  title: 'готово',
  error: null,
  report: { prepared: 3 },
  retries_left: 3,
  next_try_at: null,
  ended_at: '2026-09-24T12:00:00Z',
};

describe('исход задачи', () => {
  it('ждущая повтора названа с причиной, временем и остатком попыток', () => {
    const text = jobSentence({
      ...BASE,
      state: 'retry_wait',
      title: 'ждёт повтора',
      error: 'ConnectError: сеть',
      retries_left: 2,
      next_try_at: '2026-09-24T12:02:00Z',
    });

    expect(text).toContain('Сборка писем: ждёт повтора');
    expect(text).toContain('ConnectError: сеть');
    expect(text).toContain('осталось попыток: 2');
    expect(text).toContain('следующая попытка');
  });

  it('постоянный отказ назван как «не выполнена» и с причиной', () => {
    const text = jobSentence({
      ...BASE,
      state: 'refused',
      title: 'не выполнена',
      error: 'TemplateError: в шаблоне нет зоны offer',
    });

    expect(text).toBe('Сборка писем: не выполнена — TemplateError: в шаблоне нет зоны offer');
  });

  it('строка сама спрашивает задачу и один раз говорит, что та кончилась', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/jobs/job-7': { body: BASE } });
    const finished = vi.fn();

    renderWith(<JobLine jobId="job-7" onFinished={finished} />);

    expect(await screen.findByRole('status')).toHaveTextContent('Сборка писем: готово');
    expect(finished).toHaveBeenCalledTimes(1);
  });

  it('новая задача под тем же номером: строка спрашивает заново и говорит и о её конце', async () => {
    // Сборка и пачка писем — одна на этап за раз, номер постоянный (проверка QA 10.10.2026):
    // без нового вопроса строка так и показывала бы итог прежней задачи.
    let state: JobCard = BASE;
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/jobs/job-7': () => ({ body: state }) });
    const finished = vi.fn();
    function Again() {
      const client = useQueryClient();
      return <button onClick={() => void jobRestarted(client, 'job-7')}>Поставлена снова</button>;
    }
    renderWith(
      <>
        <JobLine jobId="job-7" onFinished={finished} />
        <Again />
      </>,
    );
    const user = userEvent.setup();
    await screen.findByText('Сборка писем: готово');

    state = { ...BASE, state: 'queued', title: 'в очереди', report: null };
    await user.click(screen.getByRole('button', { name: 'Поставлена снова' }));
    expect(await screen.findByText('Сборка писем: в очереди')).toBeInTheDocument();

    state = { ...BASE, report: { prepared: 5 } };
    await user.click(screen.getByRole('button', { name: 'Поставлена снова' }));
    await screen.findByText('Сборка писем: готово');
    await waitFor(() => expect(finished).toHaveBeenCalledTimes(2));
  });
});
