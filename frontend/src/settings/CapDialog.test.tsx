/**
 * Общая смена лимита: после ответа сервера перечитывается «Расход» и те, кто ещё читает этот лимит
 * (кап Ahrefs — ещё и «Обзор»), человеку — что поставили, окно закрывается. Отказ окно не закрывает.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { useCapSave } from './CapDialog';

function saving(save: (body: number) => Promise<string>, readers?: string[]) {
  const client = new QueryClient();
  const refreshed = vi.spyOn(client, 'invalidateQueries');
  const closed = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(
    () =>
      useCapSave({
        save,
        words: (change: string) => change,
        onClose: closed,
        ...(readers === undefined ? {} : { readers }),
      }),
    { wrapper },
  );
  return { result, refreshed, closed };
}

describe('смена лимита', () => {
  it('перечитывает «Расход» и закрывает окно', async () => {
    const { result, refreshed, closed } = saving(() => Promise.resolve('сменили'));

    result.current.mutate(5);

    await waitFor(() => expect(closed).toHaveBeenCalledOnce());
    expect(refreshed.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([['usage']]);
  });

  it('и тех, кто ещё читает этот лимит', async () => {
    const { result, refreshed, closed } = saving(() => Promise.resolve('сменили'), ['overview']);

    result.current.mutate(5);

    await waitFor(() => expect(closed).toHaveBeenCalledOnce());
    expect(refreshed.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
      ['usage'],
      ['overview'],
    ]);
  });

  it('отказ сервера окно не закрывает и ничего не перечитывает', async () => {
    const { result, refreshed, closed } = saving(() => Promise.reject(new Error('отказ')));

    result.current.mutate(5);

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(closed).not.toHaveBeenCalled();
    expect(refreshed).not.toHaveBeenCalled();
  });
});
