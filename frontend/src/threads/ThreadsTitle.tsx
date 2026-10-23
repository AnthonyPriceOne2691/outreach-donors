/**
 * Шапка «Диалогов» — одной строкой: заголовок, число и значок «i».
 *
 * До 09.10.2026 под заголовком стояли абзац о том, как считается состояние, строка
 * калибровки разбора и кнопка выгрузки — шапка в 220 px над таблицей в 24 строки
 * (аудит экранов 09.10.2026). Пояснение и калибровка — в подсказке «i»: их читают
 * раз, а не на каждом заходе; на экране — число.
 *
 * **Заголовок части — её пункт меню** (`THREADS_TITLES`, одни слова с `layout/sections.ts`):
 * «Диалоги с донорами», «Диалоги с рекламодателями», «Диалоги продаж». Без части — «Диалоги».
 */

import { Group, Stack, Text, Title } from '@mantine/core';

import type { Part } from '../api/stages';
import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { ParseCalibration } from './ParseCalibration';
import { THREADS_TITLES } from './threadFilters';

interface Props {
  /** Всего диалогов; `undefined` — список ещё не пришёл. */
  total: number | undefined;
  /** Сколько под фильтром; `null` — фильтра нет. */
  found: number | null;
  /** Часть списка из адреса (`?stage=…`); `null` — без части. */
  part: Part | null;
}

export function ThreadsTitle({ total, found, part }: Props) {
  return (
    <Group gap="sm" align="center" wrap="nowrap">
      <Title order={3}>{part === null ? 'Диалоги' : THREADS_TITLES[part]}</Title>
      {/* Полными чернилами, как у доноров: число стоит в углу панели,
          на блике стекла, и приглушённый тон там не держал норму. */}
      {total !== undefined && (
        <Text size="sm" c="var(--ink)" style={{ whiteSpace: 'nowrap' }}>
          {found === null
            ? `всего ${formatNumber(total)}`
            : `найдено ${formatNumber(found)} из ${formatNumber(total)}`}
        </Text>
      )}
      <InfoHint name="Как считается состояние диалога" width={340}>
        <Stack gap={6}>
          <Text size="sm" c="inherit">
            Состояние диалога считается по последнему событию: автоответчик ответом не считается, а
            отказ доставки останавливает цепочку и метит контакт.
          </Text>
          <ParseCalibration inherit />
        </Stack>
      </InfoHint>
    </Group>
  );
}
