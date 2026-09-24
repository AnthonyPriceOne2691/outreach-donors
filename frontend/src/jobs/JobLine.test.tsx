import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { JobCard } from '../api/types';
import { TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { JobLine, jobSentence } from './JobLine';

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
    serve({ 'GET /api/jobs/job-7': { body: BASE } });
    const finished = vi.fn();

    renderWith(<JobLine jobId="job-7" onFinished={finished} />);

    expect(await screen.findByRole('status')).toHaveTextContent('Сборка писем: готово');
    expect(finished).toHaveBeenCalledTimes(1);
  });
});
