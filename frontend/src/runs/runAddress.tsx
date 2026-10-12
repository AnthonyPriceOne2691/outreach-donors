/**
 * Прогон из адреса экрана — у его отчёта (`/runs/:id`) и рассмотрения
 * (`/runs/:id/review`) одно правило на двоих.
 *
 * **Номер проверяется до запроса** (`api/ids.ts`): «abc» в адресе — экран «Такого
 * прогона нет» словами, а не английский отказ разбора; номер, которого нет в базе, —
 * тот же экран с отказом сервера. **«К прогонам» — над заголовком**, на ту страницу
 * истории, с которой пришли (`state.from`): номер страницы истории живёт в её адресе.
 */

import { Card, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { useLocation, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { BackLink, backTo } from '../components/BackLink';
import { PageHead } from '../components/PageHead';

export interface RunAddress {
  /** Номер, как он стоит в адресе. */
  raw: string | undefined;
  /** Номер прогона; `null` — в адресе не номер. */
  runId: number | null;
  /** Строка параметров страницы истории, с которой пришли, — или пусто. */
  from: string;
  /** Куда ведёт «К прогонам». */
  back: string;
}

export function useRunAddress(): RunAddress {
  const raw = useParams().id;
  const from = backTo(useLocation().state);
  return { raw, runId: rowIdOf(raw), from, back: `/run${from}` };
}

/** Почему прогона нет — словами; `null` — прогон есть или ответ ещё едет. */
export function missingRun(address: RunAddress, error: unknown): string | null {
  if (address.runId === null) return `«${address.raw ?? ''}» в адресе — не номер прогона.`;
  if (error instanceof ApiError && error.status === 404) return `${refusalOf(error)}.`;
  return null;
}

interface HeadProps {
  back: string;
  title: string;
  /** Как устроен экран — в «i» у заголовка. */
  hint?: string;
  /** Факты прогона под заголовком. */
  children?: ReactNode;
}

/** Верх экрана прогона: возврат, заголовок, факты. Стоит и пока ответ едет — номер
 *  прогона известен из адреса. */
export function RunHead({ back, title, hint, children }: HeadProps) {
  return (
    <Stack gap={6}>
      <BackLink to={back}>К прогонам</BackLink>
      <PageHead title={title} {...(hint === undefined ? {} : { hint })} />
      {children}
    </Stack>
  );
}

/** Прогона нет: номер негодный или такого нет в базе. Словами и со ссылкой назад —
 *  пустое полотно или отказ разбора ничего не объясняют. `where` — как дойти до
 *  экрана прогона из истории. */
export function NoSuchRun({ back, said, where }: { back: string; said: string; where: string }) {
  return (
    <Card className="glassPanel" p="xl">
      <RunHead back={back} title="Такого прогона нет">
        <Text size="sm" c="dimmed" maw={720}>
          {said} Все прогоны — в истории на экране «Прогон»: {where}
        </Text>
      </RunHead>
    </Card>
  );
}
