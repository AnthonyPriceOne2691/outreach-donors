/**
 * «Собрать ещё N» гипотезе плана: цель прибавляется, и гипотеза снова собирает — сразу или когда
 * дойдёт очередь (собирает одна гипотеза плана). Ключей и глубины не осталось — сервер сразу
 * ставит «нужны ключи», и в колонке встаёт «Добавить ключи».
 *
 * Отказ сервера — словами над полем; набранное остаётся.
 */

import { NumberInput, Text } from '@mantine/core';
import { useState } from 'react';

import { formatNumber } from '../format';
import { notify } from '../notices';
import { collectMore, usePlanAction } from './planApi';
import type { PlanRow } from './planApi';
import { PlanDialog } from './PlanDialog';

/** Цель гипотезы — не больше, как у книги плана (`plan_card.MAX_TARGET`). */
export const MAX_TARGET = 1_000_000;

interface Props {
  row: PlanRow;
  onClose: () => void;
}

export function PlanMore({ row, onClose }: Props) {
  const [more, setMore] = useState<number | ''>('');
  const send = usePlanAction(
    (n: number) => collectMore(row.id, n),
    (found) => {
      notify({
        message: `«${row.name}»: цель — ${formatNumber(found.target)} сайтов`,
        color: 'green',
      });
      onClose();
    },
  );
  const n = typeof more === 'number' ? more : 0;
  return (
    <PlanDialog
      title={`Собрать ещё: «${row.name}»`}
      refused="Цель не поправлена"
      error={send.error}
      busy={send.isPending}
      ready={n >= 1}
      action="Собрать ещё"
      onSubmit={() => send.mutate(n)}
      onClose={onClose}
    >
      <NumberInput
        label="Сколько сайтов ещё найти"
        description={`Сейчас: ${formatNumber(row.found)} из ${formatNumber(row.target)}`}
        min={1}
        max={MAX_TARGET - row.target}
        allowDecimal={false}
        allowNegative={false}
        thousandSeparator=" "
        value={more}
        data-autofocus
        onChange={(value) => setMore(typeof value === 'number' ? value : '')}
      />
      {n >= 1 && (
        <Text size="sm" c="dimmed">
          Цель станет {formatNumber(row.target + n)}. Если ключи и глубина кончились, гипотеза сразу
          встанет со словами «нужны ключи».
        </Text>
      )}
    </PlanDialog>
  );
}
